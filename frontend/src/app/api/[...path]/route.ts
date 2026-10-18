import { proxy } from "@/lib/backend";

/** Event streams of long tasks, and long polls, stay open a while. */
export const maxDuration = 300;

type Context = RouteContext<"/api/[...path]">;

/** `/api/*`: forwards the allowed routes to the backend (see `ROUTES`). */
async function handle(request: Request, context: Context): Promise<Response> {
  const { path } = await context.params;
  return proxy(request, path);
}

export function GET(request: Request, context: Context): Promise<Response> {
  return handle(request, context);
}

export function POST(request: Request, context: Context): Promise<Response> {
  return handle(request, context);
}

export function DELETE(request: Request, context: Context): Promise<Response> {
  return handle(request, context);
}
