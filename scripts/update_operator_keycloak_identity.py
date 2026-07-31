"""Script to update live Keycloak operator@zensynq.com tenant_id attribute to '__operator__'.

Executes against live Keycloak REST Admin API and verifies resulting OIDC JWT claims.
"""
import os
import json
import httpx
import jwt

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080" if os.path.exists("/.dockerenv") else "http://localhost:8080")
ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN", "admin")
ADMIN_PASS = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin_password_change_me")


def update_operator_identity():
    # 1. Get Admin Access Token
    token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    resp = httpx.post(
        token_url,
        data={
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    admin_token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    # 2. Get User ID for operator@zensynq.com
    users_url = f"{KEYCLOAK_URL}/admin/realms/hms/users?username=operator@zensynq.com"
    resp = httpx.get(users_url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    users = resp.json()
    if not users:
        raise RuntimeError("operator@zensynq.com not found in Keycloak hms realm")
    user = users[0]
    user_id = user["id"]

    # 3. Update User Attributes
    user["attributes"] = user.get("attributes", {})
    user["attributes"]["tenant_id"] = ["__operator__"]

    update_url = f"{KEYCLOAK_URL}/admin/realms/hms/users/{user_id}"
    resp = httpx.put(update_url, headers=headers, json=user, timeout=10.0)
    resp.raise_for_status()
    print(f"✓ Keycloak user '{user['username']}' attribute 'tenant_id' updated to '__operator__'.")

    # 4. Authenticate as operator and decode OIDC token
    op_token_url = f"{KEYCLOAK_URL}/realms/hms/protocol/openid-connect/token"
    resp = httpx.post(
        op_token_url,
        data={
            "client_id": "hms-web",
            "grant_type": "password",
            "username": "operator@zensynq.com",
            "password": "Password123!",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    tokens = resp.json()
    access_token = tokens["access_token"]
    decoded = jwt.decode(access_token, options={"verify_signature": False})
    
    print("\n--- Decoded Operator JWT Claims ---")
    print(json.dumps(decoded, indent=2))
    print(f"app.tenant_id claim: {decoded.get('app.tenant_id')}")
    assert decoded.get("app.tenant_id") == "__operator__", f"Expected '__operator__', got {decoded.get('app.tenant_id')}"
    print("✓ VERIFIED: operator JWT claim app.tenant_id is '__operator__'.")


if __name__ == "__main__":
    update_operator_identity()
