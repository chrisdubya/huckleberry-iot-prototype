#pragma once
/* Join the configured network (reconnecting forever) and block until it has an IP. */
void wifi_connect(const char *ssid, const char *password);
/* Start SNTP and block until the clock is set. */
void wifi_sync_time(void);
