import client from "./client";

export interface ProxyStatus {
  running: boolean;
  host: string;
  port: number;
}

export interface CertInfo {
  available: boolean;
  pem_path: string;
  der_path: string;
  installed: boolean;
}

export interface CertInstallResult {
  success: boolean;
  message: string;
}

/** 获取代理状态 */
export async function getProxyStatus(): Promise<ProxyStatus> {
  const { data } = await client.get<ProxyStatus>("/proxy/status");
  return data;
}

/** 启动代理 */
export async function startProxy(): Promise<ProxyStatus> {
  const { data } = await client.post<ProxyStatus>("/proxy/start");
  return data;
}

/** 停止代理 */
export async function stopProxy(): Promise<ProxyStatus> {
  const { data } = await client.post<ProxyStatus>("/proxy/stop");
  return data;
}

/** 获取证书状态 */
export async function getCertInfo(): Promise<CertInfo> {
  const { data } = await client.get<CertInfo>("/proxy/cert/info");
  return data;
}

/** 安装证书到系统信任存储 */
export async function installCert(): Promise<CertInstallResult> {
  const { data } = await client.post<CertInstallResult>("/proxy/cert/install");
  return data;
}

/** 卸载证书 */
export async function uninstallCert(): Promise<CertInstallResult> {
  const { data } = await client.delete<CertInstallResult>("/proxy/cert/uninstall");
  return data;
}

/** 获取证书下载 URL */
export function getCertDownloadUrl(): string {
  const isTauri = "__TAURI_INTERNALS__" in window || "__TAURI__" in window;
  const base = isTauri ? "http://127.0.0.1:9876/api" : "/api";
  return `${base}/proxy/cert/download`;
}
