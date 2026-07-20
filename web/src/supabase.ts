import { createClient } from "@supabase/supabase-js";

const supabaseUrl = "https://xokvgxwdymctvdlidqpf.supabase.co";
const publishableKey = "sb_publishable_LE-y6dFNR8jYetqS3kBU6Q_GxKufngm";

export const supabase = createClient(supabaseUrl, publishableKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: "pkce",
  },
});
