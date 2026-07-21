create index user_notifications_watch_request_idx
  on public.user_notifications (watch_request_id, created_at desc);
