"""
Set Firebase custom claim { admin: true } on a user account.

Usage:
    python scripts/set_admin_claim.py guesmitaha96@gmail.com

Requires: firebase-admin (pip install firebase-admin)
Uses application default credentials (gcloud auth application-default login)
"""
import sys
import firebase_admin
from firebase_admin import auth

def main():
    email = sys.argv[1] if len(sys.argv) > 1 else "guesmitaha96@gmail.com"

    # Use application default credentials (works after: gcloud auth application-default login)
    firebase_admin.initialize_app()

    user = auth.get_user_by_email(email)
    current_claims = user.custom_claims or {}
    print(f"User: {user.email}  (uid={user.uid})")
    print(f"Current claims: {current_claims}")

    auth.set_custom_user_claims(user.uid, {**current_claims, "admin": True})
    print(f"✅  admin: true  set on  {user.email}")
    print()
    print("The user must sign out and back in (or wait ~1 hour) for the new claim to take effect.")
    print("Force-refresh immediately: user.getIdToken(true) in the browser console.")

if __name__ == "__main__":
    main()
