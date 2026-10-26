import type { Message } from "@/lib/api/types";

export const CONV_ID = "11111111-1111-4111-8111-111111111111";

export function message(over: Partial<Message> = {}): Message {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    conversation_id: CONV_ID,
    role: "user",
    content: "Hello",
    status: "complete",
    error_code: null,
    model: null,
    created_at: "2026-10-26T09:00:00Z",
    ...over,
  };
}
