import client from "./client";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  PayloadRequest,
  PayloadResponse,
} from "../types";

export async function analyzeFlow(
  req: AnalyzeRequest
): Promise<AnalyzeResponse> {
  const { data } = await client.post<AnalyzeResponse>("/ai/analyze", req);
  return data;
}

export async function generatePayloads(
  req: PayloadRequest
): Promise<PayloadResponse> {
  const { data } = await client.post<PayloadResponse>("/ai/payloads", req);
  return data;
}
