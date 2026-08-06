// Hemlock Tauri 主入口 - 防止 Windows 上出现控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    hemlock_lib::run()
}
