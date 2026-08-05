import client from "./client";
import type { SendRequestRequest, SendRequestResponse } from "../types";

export async function sendRequest(
  req: SendRequestRequest
): Promise<SendRequestResponse> {
  const { data } = await client.post<SendRequestResponse>("/request/send", req);
  return data;
}
