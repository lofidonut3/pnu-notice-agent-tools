import { createClient } from "npm:@supabase/supabase-js@2.110.7";

const corsHeaders = {
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Origin": "https://lofidonut3.github.io",
  "Content-Type": "application/json",
};

function json(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders });
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }

  const authorization = request.headers.get("Authorization");
  if (!authorization) {
    return json({ error: "authentication_required" }, 401);
  }

  let requestId = "";
  try {
    const body = await request.json();
    requestId = String(body?.request_id ?? "").trim();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) {
    return json({ error: "invalid_request_id" }, 400);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const publishableKey = Deno.env.get("SUPABASE_ANON_KEY");
  if (!supabaseUrl || !publishableKey) {
    return json({ error: "function_not_configured" }, 503);
  }

  const client = createClient(supabaseUrl, publishableKey, {
    global: { headers: { Authorization: authorization } },
    auth: { persistSession: false },
  });
  const { data: authData, error: authError } = await client.auth.getUser();
  if (authError || !authData.user) {
    return json({ error: "authentication_required" }, 401);
  }

  const { data: watch, error: watchError } = await client
    .from("watch_requests")
    .select("id,status,updated_at")
    .eq("id", requestId)
    .single();
  if (watchError || !watch) {
    return json({ error: "watch_not_found" }, 404);
  }

  const dispatchToken = Deno.env.get("GITHUB_DISPATCH_TOKEN");
  if (!dispatchToken) {
    return json({ accepted: false, fallback: "schedule", error: "dispatch_not_configured" }, 503);
  }

  const owner = Deno.env.get("GITHUB_REPOSITORY_OWNER") ?? "lofidonut3";
  const repository = Deno.env.get("GITHUB_REPOSITORY_NAME") ?? "pnu-notice-agent-tools";
  const response = await fetch(`https://api.github.com/repos/${owner}/${repository}/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${dispatchToken}`,
      "Content-Type": "application/json",
      "User-Agent": "pnu-watch-dispatcher",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({
      event_type: "pnu-watch-requested",
      client_payload: {
        request_id: requestId,
        requested_by: authData.user.id,
        watch_status: watch.status,
      },
    }),
  });

  if (!response.ok) {
    const requestMarker = response.headers.get("x-github-request-id");
    return json(
      {
        accepted: false,
        fallback: "schedule",
        error: "dispatch_failed",
        upstream_status: response.status,
        request_marker: requestMarker,
      },
      502,
    );
  }

  return json({ accepted: true, request_id: requestId }, 202);
});
