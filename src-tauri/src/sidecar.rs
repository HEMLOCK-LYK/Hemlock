// Python 后端 sidecar 管理
// 开发阶段：直接运行 venv 中的 python
// 生产阶段：运行 PyInstaller 打包的后端可执行文件

use tauri::{App, AppHandle, Manager};
use std::process::Command;
use std::time::{Duration, Instant};
use std::thread;
use std::path::PathBuf;

use crate::BackendState;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const BACKEND_PORT: &str = "9876";
const BACKEND_HOST: &str = "127.0.0.1";
const HEALTH_CHECK_TIMEOUT_SECS: u64 = 20;

/// 获取应用数据目录（传递给后端作为 HEMLOCK_DATA_DIR）
fn get_data_dir(app: &App) -> PathBuf {
    // 优先使用 Tauri 的 app_data_dir（Windows 上为 %APPDATA%/<identifier>）
    if let Ok(data_dir) = app.path().app_data_dir() {
        return data_dir;
    }
    // 回退：使用当前 exe 所在目录
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            return exe_dir.join("data");
        }
    }
    PathBuf::from("data")
}

/// 查找后端可执行文件，按优先级尝试多个路径
fn find_backend_exe(app: &App) -> Option<(PathBuf, PathBuf)> {
    // 1. 尝试 Tauri resource_dir（安装后的标准路径）
    if let Ok(res_dir) = app.path().resource_dir() {
        // resource_dir/hemlock-backend/hemlock-backend.exe
        let exe = res_dir.join("hemlock-backend").join("hemlock-backend.exe");
        if exe.exists() {
            return Some((exe, res_dir.join("hemlock-backend")));
        }
        // resource_dir/hemlock-backend.exe（兼容旧路径）
        let exe = res_dir.join("hemlock-backend.exe");
        if exe.exists() {
            return Some((exe, res_dir));
        }
    }

    // 2. 尝试 exe 同级目录下的 resources（--no-bundle 模式）
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let res_dir = exe_dir.join("resources").join("hemlock-backend");
            let exe = res_dir.join("hemlock-backend.exe");
            if exe.exists() {
                return Some((exe, res_dir));
            }
        }
    }

    // 3. 尝试项目开发目录
    let workspace_root = std::env::current_dir()
        .map(|p| {
            p.parent()
                .and_then(|p| p.parent())
                .unwrap_or(&p)
                .to_path_buf()
        })
        .unwrap_or_else(|_| PathBuf::from(".."));

    let dev_res = workspace_root.join("src-tauri").join("resources").join("hemlock-backend");
    let exe = dev_res.join("hemlock-backend.exe");
    if exe.exists() {
        return Some((exe, dev_res));
    }

    None
}

/// 启动后端进程（非阻塞）：仅 spawn 进程并存储，立即返回。
/// 健康检查由 wait_for_backend_ready 在后台线程执行，不阻塞窗口创建。
pub fn start_backend(app: &App) -> Result<(), Box<dyn std::error::Error>> {
    let workspace_root = std::env::current_dir()
        .map(|p| {
            p.parent()
                .and_then(|p| p.parent())
                .unwrap_or(&p)
                .to_path_buf()
        })
        .unwrap_or_else(|_| PathBuf::from(".."));

    // 判断是开发模式还是生产模式
    let is_dev = cfg!(debug_assertions) || std::env::var("HEMLOCK_DEV").is_ok();

    let (cmd, cmd_args, working_dir) = if is_dev {
        // 开发模式：使用 venv 中的 python
        let python_path = workspace_root
            .join("backend")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");

        let python = if python_path.exists() {
            python_path
        } else {
            PathBuf::from("python")
        };

        let backend_dir = workspace_root.join("backend");

        println!("[Hemlock] Dev mode: starting backend with {:?} in {:?}", python, backend_dir);
        (
            python,
            vec![
                "-m".to_string(),
                "uvicorn".to_string(),
                "hemlock.main:app".to_string(),
                "--host".to_string(),
                BACKEND_HOST.to_string(),
                "--port".to_string(),
                BACKEND_PORT.to_string(),
            ],
            backend_dir,
        )
    } else {
        // 生产模式：查找打包的后端可执行文件
        match find_backend_exe(app) {
            Some((backend_exe, backend_dir)) => {
                println!("[Hemlock] Production mode: starting bundled backend {:?}", backend_exe);
                (
                    backend_exe,
                    vec![
                        "--host".to_string(),
                        BACKEND_HOST.to_string(),
                        "--port".to_string(),
                        BACKEND_PORT.to_string(),
                    ],
                    backend_dir,
                )
            }
            None => {
                // 回退到系统 Python
                println!("[Hemlock] Fallback: backend exe not found, trying system python");
                let backend_dir = workspace_root.join("backend");
                (
                    PathBuf::from("python"),
                    vec![
                        "-m".to_string(),
                        "uvicorn".to_string(),
                        "hemlock.main:app".to_string(),
                        "--host".to_string(),
                        BACKEND_HOST.to_string(),
                        "--port".to_string(),
                        BACKEND_PORT.to_string(),
                    ],
                    backend_dir,
                )
            }
        }
    };

    // 启动后端进程（Windows 下隐藏控制台窗口）
    let data_dir = get_data_dir(app);
    // 确保数据目录存在
    let _ = std::fs::create_dir_all(&data_dir);

    let mut cmd = Command::new(&cmd);
    cmd.args(&cmd_args).current_dir(&working_dir);
    // 传递数据目录给后端，使所有数据文件写入统一位置
    cmd.env("HEMLOCK_DATA_DIR", &data_dir);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let child = cmd.spawn()?;

    let state = app.state::<BackendState>();
    *state.0.lock().unwrap() = Some(child);

    println!("[Hemlock] Backend process spawned, health check running in background...");

    Ok(())
}

/// 后台线程：等待后端就绪（非阻塞，在独立线程中运行）。
/// 窗口和前端已在此期间显示，前端会自动重试连接后端。
pub fn wait_for_backend_ready() {
    thread::spawn(move || {
        let backend_url = format!("http://{}:{}/health", BACKEND_HOST, BACKEND_PORT);
        let start = Instant::now();
        let mut ready = false;

        while start.elapsed() < Duration::from_secs(HEALTH_CHECK_TIMEOUT_SECS) {
            thread::sleep(Duration::from_millis(500));
            if check_backend_health(&backend_url) {
                ready = true;
                break;
            }
        }

        if ready {
            println!("[Hemlock] Backend started successfully (ready in {:.1}s)", start.elapsed().as_secs_f64());
        } else {
            println!("[Hemlock] WARNING: Backend health check timeout, frontend may not connect properly");
        }
    });
}

fn check_backend_health(url: &str) -> bool {
    // 简单的 TCP 连接检查（避免引入 HTTP 依赖）
    use std::net::TcpStream;
    let host_port = url
        .strip_prefix("http://")
        .and_then(|s| s.strip_suffix("/health"))
        .unwrap_or("127.0.0.1:9876");

    TcpStream::connect_timeout(
        &host_port.parse().unwrap_or("127.0.0.1:9876".parse().unwrap()),
        Duration::from_millis(500),
    ).is_ok()
}

pub fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let child = state.0.lock().unwrap().take();
    if let Some(child) = child {
        let pid = child.id();
        println!("[Hemlock] Stopping backend (PID={})...", pid);

        #[cfg(windows)]
        {
            // Windows: 使用 taskkill /T /F 杀掉整个进程树
            // PyInstaller 后端可能 spawn 了 mitmproxy 等子进程，
            // child.kill() 只杀直接子进程，子进程会变成孤儿继续运行
            let output = Command::new("taskkill")
                .args(["/T", "/F", "/PID", &pid.to_string()])
                .creation_flags(CREATE_NO_WINDOW)
                .output();

            match output {
                Ok(o) if o.status.success() => {
                    println!("[Hemlock] Backend process tree killed (PID={})", pid);
                }
                _ => {
                    // taskkill 失败（可能进程已退出），回退到 child.kill()
                    println!("[Hemlock] taskkill failed, falling back to child.kill()");
                    let mut child = child;
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }

        #[cfg(not(windows))]
        {
            // Unix: child.kill() 会发送 SIGKILL，但子进程可能不受影响
            // 使用 kill -- -PID 杀进程组（需要 setpgid，这里简化为 kill 子进程）
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
        }

        println!("[Hemlock] Backend stopped");
    }
}
