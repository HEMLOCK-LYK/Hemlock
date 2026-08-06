import client from "./client";
import type { IntruderConfig, IntruderResult } from "../types";

export async function runIntruder(
  config: IntruderConfig
): Promise<IntruderResult> {
  const { data } = await client.post<IntruderResult>("/intruder/run", config);
  return data;
}

export async function getIntruderResult(
  taskId: string
): Promise<IntruderResult> {
  const { data } = await client.get<IntruderResult>(
    `/intruder/${taskId}/result`
  );
  return data;
}
