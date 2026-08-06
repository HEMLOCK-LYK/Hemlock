"""代理控制 API：启动/停止/状态查询 + 证书管理。"""

import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from hemlock.core.proxy_controller import proxy_controller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

# mitmproxy CA 证书默认目录
MITMPROXY_CA_DIR = Path.home() / ".mitmproxy"
# PEM 格式证书（通用）
CA_CERT_PEM = MITMPROXY_CA_DIR / "mitmproxy-ca-cert.pem"
# DER 格式证书（Windows 安装用）
CA_CERT_DER = MITMPROXY_CA_DIR / "mitmproxy-ca-cert.cer"


# certutil 完整路径（避免 PATH 环境问题）
CERTUTIL_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "certutil.exe")


class ProxyStatusResponse(BaseModel):
    running: bool
    host: str
    port: int
    error: str = ""


class CertInfoResponse(BaseModel):
    available: bool
    pem_path: str
    der_path: str
    installed: bool


class CertInstallResponse(BaseModel):
    success: bool
    message: str


@router.get("/status", response_model=ProxyStatusResponse)
async def get_proxy_status():
    """获取代理运行状态。"""
    s = proxy_controller.status()
    return ProxyStatusResponse(
        running=s["running"],
        host=s["host"],
        port=s["port"],
    )


@router.post("/start", response_model=ProxyStatusResponse)
async def start_proxy():
    """启动抓包代理。"""
    success, message = proxy_controller.start()
    s = proxy_controller.status()
    return ProxyStatusResponse(
        running=s["running"],
        host=s["host"],
        port=s["port"],
        error="" if success else message,
    )


@router.post("/stop", response_model=ProxyStatusResponse)
async def stop_proxy():
    """停止抓包代理。"""
    success, message = proxy_controller.stop()
    s = proxy_controller.status()
    return ProxyStatusResponse(
        running=s["running"],
        host=s["host"],
        port=s["port"],
        error="" if success else message,
    )


def _ensure_ca_cert() -> Path:
    """确保证书存在，如果不存在则尝试启动一次代理来生成。"""
    # 如果 PEM 证书已存在，直接返回
    if CA_CERT_PEM.exists():
        return CA_CERT_PEM

    # 证书不存在，尝试启动代理来触发 mitmproxy 生成证书
    if not proxy_controller.running:
        proxy_controller.start()

    # 等待证书生成（最多 5 秒）
    import time
    for _ in range(10):
        if CA_CERT_PEM.exists():
            break
        time.sleep(0.5)

    if CA_CERT_PEM.exists():
        return CA_CERT_PEM

    raise HTTPException(
        status_code=404,
        detail="CA 证书未生成，请先启动代理后再尝试获取证书",
    )


@router.get("/cert/info", response_model=CertInfoResponse)
async def get_cert_info():
    """获取 CA 证书状态信息。"""
    pem_exists = CA_CERT_PEM.exists()
    der_exists = CA_CERT_DER.exists()

    # 检查是否已安装到 Windows 证书存储
    installed = False
    if sys.platform == "win32" and pem_exists:
        try:
            result = subprocess.run(
                [CERTUTIL_PATH, "-store", "Root", "mitmproxy"],
                capture_output=True,
                timeout=5,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            # certutil 输出为 GBK 编码（中文 Windows），需手动解码
            output = (
                (result.stdout or b"").decode("gbk", errors="replace")
                + (result.stderr or b"").decode("gbk", errors="replace")
            )
            # certutil -store 即使找到证书也可能返回非零退出码，
            # 改用输出内容匹配 "mitmproxy" 来判断
            installed = "mitmproxy" in output.lower()
        except Exception:
            installed = False

    return CertInfoResponse(
        available=pem_exists,
        pem_path=str(CA_CERT_PEM),
        der_path=str(CA_CERT_DER),
        installed=installed,
    )


@router.get("/cert/download")
async def download_cert():
    """下载 CA 证书文件（PEM 格式）。"""
    cert_path = _ensure_ca_cert()
    cert_bytes = cert_path.read_bytes()
    return Response(
        content=cert_bytes,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": "attachment; filename=mitmproxy-ca-cert.pem",
        },
    )


@router.post("/cert/install", response_model=CertInstallResponse)
async def install_cert():
    """安装 CA 证书到系统信任存储。

    Windows: 使用 certutil 安装到 Root 证书存储（需要管理员权限）。
    """
    if sys.platform != "win32":
        return CertInstallResponse(
            success=False,
            message="仅支持 Windows 自动安装，请手动安装证书",
        )

    cert_path = _ensure_ca_cert()

    try:
        # 使用 certutil 安装到 Root（受信任的根证书颁发机构）
        result = subprocess.run(
            [CERTUTIL_PATH, "-addstore", "Root", str(cert_path)],
            capture_output=True,
            timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )

        if result.returncode == 0:
            return CertInstallResponse(
                success=True,
                message="证书已成功安装到「受信任的根证书颁发机构」",
            )
        else:
            # 可能需要管理员权限（stderr/stdout 为 GBK 编码 bytes）
            stderr = (result.stderr or b"").decode("gbk", errors="replace").strip()
            stdout = (result.stdout or b"").decode("gbk", errors="replace").strip()
            error_msg = stderr or stdout
            return CertInstallResponse(
                success=False,
                message=f"安装失败（可能需要管理员权限）：{error_msg}",
            )
    except FileNotFoundError:
        return CertInstallResponse(
            success=False,
            message="certutil 命令不可用",
        )
    except subprocess.TimeoutExpired:
        return CertInstallResponse(
            success=False,
            message="安装超时，请尝试手动安装",
        )
    except Exception as e:
        return CertInstallResponse(
            success=False,
            message=f"安装失败：{e}",
        )


@router.delete("/cert/uninstall", response_model=CertInstallResponse)
async def uninstall_cert():
    """从系统信任存储卸载 CA 证书。"""
    if sys.platform != "win32":
        return CertInstallResponse(
            success=False,
            message="仅支持 Windows 自动卸载",
        )

    try:
        result = subprocess.run(
            [CERTUTIL_PATH, "-delstore", "Root", "mitmproxy"],
            capture_output=True,
            timeout=15,
            creationflags=0x08000000,
        )

        if result.returncode == 0:
            return CertInstallResponse(
                success=True,
                message="证书已从「受信任的根证书颁发机构」移除",
            )
        else:
            stderr = (result.stderr or b"").decode("gbk", errors="replace").strip()
            stdout = (result.stdout or b"").decode("gbk", errors="replace").strip()
            error_msg = stderr or stdout
            return CertInstallResponse(
                success=False,
                message=f"卸载失败：{error_msg}",
            )
    except Exception as e:
        return CertInstallResponse(
            success=False,
            message=f"卸载失败：{e}",
        )
