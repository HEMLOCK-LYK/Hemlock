// Hemlock Tauri 库入口

pub mod sidecar;

use tauri::Manager;
use std::sync::Mutex;
use std::process::Child;

/// 全局存储后端子进程
pub struct BackendState(pub Mutex<Option<Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            // 非阻塞启动后端进程，窗口立即创建显示启动屏
            sidecar::start_backend(app)?;
            // 后台线程等待后端就绪（不阻塞窗口创建）
            sidecar::wait_for_backend_ready();

            // Rust 端控制窗口显示：延迟 150ms 确保 WebView2 已渲染 splash HTML
            // 不依赖前端 JS 调用 win.show()，避免前端加载失败导致窗口永远不可见
            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(150));
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // 窗口关闭/销毁时：杀掉后端进程树，确保所有子进程退出
            if let tauri::WindowEvent::Destroyed = event {
                sidecar::stop_backend(window.app_handle());
            }
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                sidecar::stop_backend(window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Hemlock application");
}
