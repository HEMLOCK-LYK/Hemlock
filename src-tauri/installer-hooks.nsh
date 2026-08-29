;; Hemlock NSIS 安装钩子（Tauri installerHooks）
;;
;; 背景：Tauri 安装器的运行检查只覆盖主程序 Hemlock.exe；后端 hemlock-backend.exe
;; 常驻运行（含升级时旧实例未退出的场景）会锁定自身 EXE 与依赖 DLL，
;; 导致安装阶段「文件被占用」覆盖失败。
;; 在文件复制前先结束两类进程；进程不存在时 taskkill 返回非零，忽略即可。

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "正在结束 Hemlock 后端进程（避免 DLL 被占用）..."
  nsExec::Exec 'taskkill /F /IM hemlock-backend.exe /T'
  nsExec::Exec 'taskkill /F /IM Hemlock.exe /T'
  Sleep 1500
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; 卸载时同样清理可能残留的后端进程
  nsExec::Exec 'taskkill /F /IM hemlock-backend.exe /T'
!macroend
