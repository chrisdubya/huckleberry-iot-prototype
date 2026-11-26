/* Firebase email/password sign-in and token refresh, as the Huckleberry app
 * (and py-huckleberry-api) do it. The ID token is a bearer token for Firestore. */
#pragma once

#include <stdbool.h>

/* Firebase project the Huckleberry app talks to (py-huckleberry-api const.py). */
#define FIREBASE_API_KEY "AIzaSyApGVHktXeekGyAt-G6dIeWHUkq2oXqcjg"
#define FIREBASE_PROJECT_ID "simpleintervals"
#define FIRESTORE_BASE_URL "https://firestore.googleapis.com/v1/projects/" FIREBASE_PROJECT_ID "/databases/(default)/documents"

void firebase_auth_init(const char *email, const char *password);
/* Sign in, or refresh if the token expires within 5 minutes (api.ensure_session). 0 on success. */
int firebase_auth_ensure(void);
/* Force a fresh sign-in (used when a request comes back 401). */
int firebase_auth_signin(void);
/* Current ID token (valid after firebase_auth_ensure); NULL if never signed in. */
const char *firebase_auth_id_token(void);
const char *firebase_auth_user_uid(void);
