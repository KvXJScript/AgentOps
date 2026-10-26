import { proxyRequest } from "@/lib/server/proxy";

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return proxyRequest(req, path);
}

export {
  handler as GET,
  handler as POST,
  handler as PUT,
  handler as PATCH,
  handler as DELETE,
};
