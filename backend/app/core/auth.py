from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from fastapi import Request, Depends
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def create_token(data: dict, expires_minutes: int = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None

def get_current_user(request: Request, db: Session = Depends(get_db)):
    from app.models import Utilisateur
    token = request.cookies.get("scholarsync_session")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return db.query(Utilisateur).filter(
        Utilisateur.email == payload.get("sub"),
        Utilisateur.actif == True
    ).first()

def require_auth(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=307,
            headers={"Location": f"/admin/connexion?next={request.url.path}"})
    return user

def require_super_admin(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if user.role != "super_admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403)
    return user

def send_reset_email(to_email: str, reset_url: str, params: dict):
    """Envoie le mail de réinitialisation via SMTP configuré."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    host = params.get("smtp_host", "")
    port = int(params.get("smtp_port", "587"))
    user = params.get("smtp_user", "")
    password = params.get("smtp_password", "")
    from_name = params.get("smtp_from_name", params.get("nom_outil", "ScholarSync"))
    from_email = params.get("smtp_from_email", user)
    nom_outil = params.get("nom_outil", "ScholarSync")

    if not host or not user:
        print(f"[RESET] Lien pour {to_email}: {reset_url}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Réinitialisation de mot de passe — {nom_outil}"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:2rem;">
      <h2 style="color:#1a3a5c;">{nom_outil}</h2>
      <p>Vous avez demandé la réinitialisation de votre mot de passe.</p>
      <p>Cliquez sur le bouton ci-dessous pour choisir un nouveau mot de passe :</p>
      <a href="{reset_url}" style="display:inline-block;background:#1a3a5c;color:#fff;padding:.75rem 1.5rem;border-radius:8px;text-decoration:none;font-weight:600;margin:1rem 0;">
        Réinitialiser mon mot de passe
      </a>
      <p style="color:#888;font-size:.85rem;">Ce lien expire dans 1 heure. Si vous n'avez pas fait cette demande, ignorez cet email.</p>
      <hr style="border:none;border-top:1px solid #eee;margin:1.5rem 0;">
      <p style="color:#aaa;font-size:.75rem;">{nom_outil} — {params.get("institution_nom", "")}</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            if port == 587:
                server.starttls()
            if password:
                server.login(user, password)
            server.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[SMTP ERROR] {e}")
        print(f"[RESET] Lien pour {to_email}: {reset_url}")
        return False
