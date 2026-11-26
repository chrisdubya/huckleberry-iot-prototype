#include "firebase_auth.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "http.h"
#include "platform.h"

#define AUTH_URL "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" FIREBASE_API_KEY
#define REFRESH_URL "https://securetoken.googleapis.com/v1/token?key=" FIREBASE_API_KEY

static const char *TAG = "auth";

static struct {
    const char *email, *password;
    char *id_token;
    char *refresh_token;
    char uid[64];
    double expires_at;
} A;

void firebase_auth_init(const char *email, const char *password)
{
    A.email = email;
    A.password = password;
}

static void take_string(char **slot, const cJSON *obj, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (!cJSON_IsString(v))
        return;
    free(*slot);
    *slot = strdup(v->valuestring);
}

static int expires_in(const cJSON *obj, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (cJSON_IsString(v))
        return atoi(v->valuestring);
    if (cJSON_IsNumber(v))
        return (int)v->valuedouble;
    return 3600;
}

static int signin_locked(void)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "email", A.email);
    cJSON_AddStringToObject(req, "password", A.password);
    cJSON_AddBoolToObject(req, "returnSecureToken", true);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);

    http_response_t resp = {0};
    int rc = http_request("POST", AUTH_URL, NULL, body, &resp);
    free(body);
    if (rc < 0 || resp.status != 200) {
        hd_log(TAG, "Authentication failed: HTTP %d %s", resp.status, resp.body ? resp.body : "");
        http_response_free(&resp);
        return -1;
    }
    cJSON *data = cJSON_Parse(resp.body);
    http_response_free(&resp);
    if (!data)
        return -1;
    take_string(&A.id_token, data, "idToken");
    take_string(&A.refresh_token, data, "refreshToken");
    const cJSON *uid = cJSON_GetObjectItemCaseSensitive(data, "localId");
    if (cJSON_IsString(uid))
        snprintf(A.uid, sizeof(A.uid), "%s", uid->valuestring);
    A.expires_at = hd_now() + expires_in(data, "expiresIn");
    cJSON_Delete(data);
    if (!A.id_token || !A.refresh_token)
        return -1;
    hd_log(TAG, "Signed in to Huckleberry as %s", A.uid);
    return 0;
}

static int refresh(void)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "grant_type", "refresh_token");
    cJSON_AddStringToObject(req, "refresh_token", A.refresh_token);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);

    http_response_t resp = {0};
    int rc = http_request("POST", REFRESH_URL, NULL, body, &resp);
    free(body);
    if (rc < 0 || resp.status != 200) {
        hd_log(TAG, "Token refresh failed: HTTP %d", resp.status);
        http_response_free(&resp);
        return resp.status >= 400 && resp.status < 500 ? 1 : -1; /* 1: token rejected, sign in again */
    }
    cJSON *data = cJSON_Parse(resp.body);
    http_response_free(&resp);
    if (!data)
        return -1;
    take_string(&A.id_token, data, "id_token");
    take_string(&A.refresh_token, data, "refresh_token");
    A.expires_at = hd_now() + expires_in(data, "expires_in");
    cJSON_Delete(data);
    hd_log(TAG, "Refreshed the Firebase token");
    return 0;
}

int firebase_auth_signin(void)
{
    hd_net_lock();
    int rc = signin_locked();
    hd_net_unlock();
    return rc;
}

int firebase_auth_ensure(void)
{
    hd_net_lock();
    int rc = 0;
    if (!A.id_token) {
        rc = signin_locked();
    } else if (hd_now() >= A.expires_at - 300) { /* refresh if it expires in less than 5 minutes */
        rc = refresh();
        if (rc == 1)
            rc = signin_locked();
    }
    hd_net_unlock();
    return rc;
}

const char *firebase_auth_id_token(void)
{
    return A.id_token;
}

const char *firebase_auth_user_uid(void)
{
    return A.uid;
}
