from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import json
import base64
from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContentWithMimeType
import asyncio
import hashlib
import secrets
import httpx
from imagekitio import ImageKit
from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'machat_db')]

# ImageKit configuration
imagekit = ImageKit(
    private_key=os.environ.get('IMAGEKIT_PRIVATE_KEY'),
    public_key=os.environ.get('IMAGEKIT_PUBLIC_KEY'),
    url_endpoint=os.environ.get('IMAGEKIT_URL_ENDPOINT')
)

# Create the main app
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
    
    async def send_personal_message(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass

manager = ConnectionManager()

# ==================== MODELS ====================

class UserPreferences(BaseModel):
    gender_preference: str  # male, female, both
    age_min: int
    age_max: int
    distance_km: int

class UserLocation(BaseModel):
    latitude: float
    longitude: float
    city: Optional[str] = None
    country: Optional[str] = None

class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phone: Optional[str] = None
    email: Optional[str] = None
    password_hash: Optional[str] = None
    name: str
    age: int
    gender: str  # male, female, other
    bio: Optional[str] = None
    preferences: UserPreferences
    location: Optional[UserLocation] = None
    photos: List[str] = []  # URLs or base64
    video_url: Optional[str] = None
    verified: bool = False
    rating_average: float = 0.0
    rating_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    language: str = "es"  # es or en
    notifications_enabled: bool = True
    auth_provider: str = "email"  # email, phone, google
    profile_visible: bool = True  # Privacy: hide profile
    blocked_users: List[str] = []  # List of blocked user IDs

class UserCreate(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    name: str
    age: int
    gender: str
    bio: Optional[str] = None
    preferences: UserPreferences
    language: str = "es"
    auth_provider: str = "email"

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

class AdminLogin(BaseModel):
    email: EmailStr
    password: str

class EmailLogin(BaseModel):
    email: EmailStr
    password: str

class EmailRegister(BaseModel):
    email: EmailStr
    password: str
    name: str
    age: int
    gender: str
    bio: Optional[str] = None
    preferences: UserPreferences
    language: str = "es"

class GoogleAuthSession(BaseModel):
    session_id: str

class SMSVerification(BaseModel):
    phone: str

class SMSVerifyCode(BaseModel):
    phone: str
    code: str

class Match(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user1_id: str
    user2_id: str
    match_type: str = "main"  # main, tricky, group
    status: str = "pending"  # pending, matched, unmatched
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class SwipeAction(BaseModel):
    target_user_id: str
    action: str  # like, pass
    match_type: str = "main"  # main, tricky, group

class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: str
    sender_id: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    read: bool = False

class MessageCreate(BaseModel):
    content: str

class GroupChat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    activity_type: str = "social"  # social, sports, outdoor, games, food, culture, other
    location: Optional[str] = None
    max_members: int = 20
    is_public: bool = True
    created_by: str
    members: List[str] = []  # user IDs
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_date: Optional[str] = None

class GroupChatCreate(BaseModel):
    name: str
    description: Optional[str] = None
    activity_type: str = "social"
    location: Optional[str] = None
    max_members: int = 20
    is_public: bool = True
    event_date: Optional[str] = None

class DateMeeting(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    match_id: str
    user1_confirmed: bool = False
    user2_confirmed: bool = False
    user1_arrived: bool = False
    user2_arrived: bool = False
    status: str = "pending"  # pending, confirmed, in_progress, completed
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class Rating(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rated_user_phone: str  # Linked to phone to prevent reset
    rater_id: str
    stars: int  # 1-5
    tags: List[str] = []  # "No se presentó", "Puntual", "Educado", etc.
    date_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class RatingCreate(BaseModel):
    stars: int
    tags: List[str] = []
    date_id: str

class Notification(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: str  # new_message, new_match, date_confirmed, date_arrival, new_rating
    content: dict
    read: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# ==================== HELPER FUNCTIONS ====================

async def create_notification(user_id: str, notif_type: str, content: dict):
    """Create and send notification to user"""
    notif = Notification(user_id=user_id, type=notif_type, content=content)
    await db.notifications.insert_one(notif.model_dump())
    
    # Send via WebSocket if connected
    await manager.send_personal_message({
        "type": "notification",
        "data": notif.model_dump()
    }, user_id)

async def check_filter_in_photo(photo_base64: str) -> bool:
    """Use Gemini Vision to detect if photo has filters"""
    try:
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            return False  # Allow if no key (MVP)
        
        chat = LlmChat(
            api_key=api_key,
            session_id=str(uuid.uuid4()),
            system_message="You are an expert at detecting photo filters and modifications."
        ).with_model("gemini", "gemini-2.0-flash")
        
        # Create message with image
        from emergentintegrations.llm.chat import ImageContent
        image_content = ImageContent(image_base64=photo_base64)
        
        user_message = UserMessage(
            text="Analyze this photo and determine if it has beauty filters, face filters, or heavy photo editing applied. Answer with only 'YES' if filters are detected or 'NO' if the photo appears natural and unfiltered.",
            file_contents=[image_content]
        )
        
        response = await chat.send_message(user_message)
        return "YES" in response.upper()
    except Exception as e:
        logging.error(f"Error checking filter: {e}")
        return False  # Allow if error (MVP)

# ==================== AUTH HELPERS ====================

def hash_password(password: str) -> str:
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    return hash_password(password) == password_hash

async def create_session(user_id: str, session_token: str):
    """Create session in database"""
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.sessions.insert_one({
        "session_token": session_token,
        "user_id": user_id,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    })

async def get_user_from_session(request: Request) -> Optional[dict]:
    """Get user from session token (cookie or header)"""
    session_token = request.cookies.get("session_token")
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ")[1]
    
    if not session_token:
        return None
    
    session = await db.sessions.find_one({"session_token": session_token})
    if not session:
        return None
    
    # Check expiration
    expires_at = datetime.fromisoformat(session["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        await db.sessions.delete_one({"session_token": session_token})
        return None
    
    user = await db.users.find_one({"id": session["user_id"]}, {"_id": 0, "password_hash": 0})
    return user

# ==================== AUTH ENDPOINTS ====================

# Email/Password Authentication
@api_router.post("/auth/email/register")
async def register_email(data: EmailRegister):
    """Register with email and password"""
    existing = await db.users.find_one({"email": data.email})
    if existing:
        # Check if profile is complete
        if existing.get("name") and existing.get("photos") and len(existing.get("photos", [])) > 0:
            raise HTTPException(status_code=400, detail="Este email ya está registrado. Inicia sesión en su lugar.")
        else:
            # Incomplete profile, delete it and allow re-registration
            logging.info(f"Removing incomplete profile for email: {data.email}")
            await db.users.delete_one({"email": data.email})
    
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        age=data.age,
        gender=data.gender,
        bio=data.bio,
        preferences=data.preferences,
        language=data.language,
        auth_provider="email"
    )
    await db.users.insert_one(user.model_dump())
    
    # Create session
    session_token = secrets.token_urlsafe(32)
    await create_session(user.id, session_token)
    
    user_dict = user.model_dump()
    user_dict.pop("password_hash", None)
    
    return {
        "success": True,
        "user": user_dict,
        "session_token": session_token
    }

@api_router.post("/auth/email/login")
async def login_email(data: EmailLogin, response: Response):
    """Login with email and password"""
    user = await db.users.find_one({"email": data.email}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    
    if not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    
    # Create session
    session_token = secrets.token_urlsafe(32)
    await create_session(user["id"], session_token)
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=7*24*60*60,  # 7 days
        path="/"
    )
    
    user.pop("password_hash", None)
    
    return {
        "success": True,
        "user": user,
        "session_token": session_token
    }

# Google OAuth Authentication
@api_router.post("/auth/google/session")
async def process_google_session(data: GoogleAuthSession, response: Response):
    """Process Google OAuth session"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": data.session_id}
            )
            resp.raise_for_status()
            session_data = resp.json()
        
        # Check if user exists
        user = await db.users.find_one({"email": session_data["email"]}, {"_id": 0})
        
        if not user:
            # Create new user (will need to complete profile later)
            user = {
                "id": str(uuid.uuid4()),
                "email": session_data["email"],
                "name": session_data.get("name", "Usuario"),
                "age": 25,  # Default, user will update
                "gender": "other",  # Default, user will update
                "preferences": {
                    "gender_preference": "both",
                    "age_min": 18,
                    "age_max": 50,
                    "distance_km": 50
                },
                "photos": [],
                "video_url": None,
                "verified": False,
                "rating_average": 0.0,
                "rating_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "language": "es",
                "notifications_enabled": True,
                "auth_provider": "google",
                "profile_complete": False
            }
            await db.users.insert_one(user)
        
        # Create session
        session_token = session_data["session_token"]
        await create_session(user["id"], session_token)
        
        # Set cookie
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            secure=True,
            samesite="none",
            max_age=7*24*60*60,
            path="/"
        )
        
        return {
            "success": True,
            "user": user,
            "session_token": session_token,
            "needs_profile_completion": user.get("profile_complete", True) == False
        }
        
    except Exception as e:
        logging.error(f"Google auth error: {e}")
        raise HTTPException(status_code=400, detail="Error al autenticar con Google")

# Phone Authentication (existing)
@api_router.post("/auth/phone/send-code")
async def send_verification_code(data: SMSVerification):
    """Send SMS verification code (mocked for MVP)"""
    mock_code = "123456"
    
    await db.verification_codes.update_one(
        {"phone": data.phone},
        {"$set": {"phone": data.phone, "code": mock_code, "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    
    return {
        "success": True,
        "message": "Código enviado (MOCK: use 123456)",
        "mock_code": mock_code
    }

@api_router.post("/auth/phone/verify-code")
async def verify_code(data: SMSVerifyCode):
    """Verify SMS code"""
    stored = await db.verification_codes.find_one({"phone": data.phone})
    
    if not stored or stored.get("code") != data.code:
        raise HTTPException(status_code=400, detail="Código inválido")
    
    user = await db.users.find_one({"phone": data.phone}, {"_id": 0, "password_hash": 0})
    
    return {
        "success": True,
        "user_exists": user is not None,
        "user": user if user else None
    }

@api_router.post("/auth/phone/register")
async def register_phone(data: UserCreate):
    """Register with phone"""
    # Check if user already exists with this phone
    existing = await db.users.find_one({"phone": data.phone})
    if existing:
        # Check if profile is complete (has name, age, photos)
        if existing.get("name") and existing.get("photos") and len(existing.get("photos", [])) > 0:
            raise HTTPException(status_code=400, detail="Este número ya tiene un perfil completo. Usa la opción de inicio de sesión.")
        else:
            # Incomplete profile, delete it and allow re-registration
            logging.info(f"Removing incomplete profile for phone: {data.phone}")
            await db.users.delete_one({"phone": data.phone})
    
    # Check email if provided
    if data.email:
        existing_email = await db.users.find_one({"email": data.email})
        if existing_email:
            raise HTTPException(status_code=400, detail="Este email ya está registrado")
    
    # Set auth_provider to phone for phone registration
    user_data = data.model_dump()
    user_data["auth_provider"] = "phone"
    user = User(**user_data)
    await db.users.insert_one(user.model_dump())
    
    # Create session
    session_token = secrets.token_urlsafe(32)
    await create_session(user.id, session_token)
    
    user_dict = user.model_dump()
    user_dict.pop("password_hash", None)
    
    return {
        "success": True,
        "user": user_dict,
        "session_token": session_token
    }

# Session Management
@api_router.get("/auth/me")
async def get_current_user(request: Request):
    """Get current authenticated user"""
    user = await get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """Logout user"""
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.sessions.delete_one({"session_token": session_token})
        response.delete_cookie("session_token", path="/")
    
    return {"success": True}

@api_router.get("/auth/imagekit")
async def get_imagekit_auth():
    """Get ImageKit authentication parameters for client-side uploads"""
    try:
        auth_params = imagekit.get_authentication_parameters()
        return {
            "token": auth_params["token"],
            "expire": auth_params["expire"],
            "signature": auth_params["signature"]
        }
    except Exception as e:
        logging.error(f"ImageKit auth error: {e}")
        raise HTTPException(status_code=500, detail="Error generating auth parameters")

# ==================== UTILITY ENDPOINTS ====================

@api_router.delete("/auth/cleanup-incomplete")
async def cleanup_incomplete_profiles():
    """Remove incomplete profiles (for development/testing)"""
    result = await db.users.delete_many({
        "$or": [
            {"name": {"$exists": False}},
            {"name": ""},
            {"photos": {"$exists": False}},
            {"photos": {"$size": 0}}
        ]
    })
    
    return {
        "success": True,
        "deleted_count": result.deleted_count,
        "message": f"Eliminados {result.deleted_count} perfiles incompletos"
    }

# ==================== PASSWORD RESET ENDPOINTS ====================

@api_router.post("/auth/password-reset/request")
async def request_password_reset(request: PasswordResetRequest):
    """Request password reset - generates token and sends email"""
    user = await db.users.find_one({"email": request.email})
    
    if not user:
        # Don't reveal if email exists for security
        return {"success": True, "message": "Si el email existe, recibirás instrucciones"}
    
    # Generate reset token
    reset_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    
    # Store token in database
    await db.password_resets.insert_one({
        "email": request.email,
        "token": reset_token,
        "expires_at": expires_at.isoformat(),
        "used": False
    })
    
    # TODO: Send email with SendGrid when API key is configured
    # For now, log the reset link
    reset_link = f"https://machat-dating-2.preview.emergentagent.com/reset-password?token={reset_token}"
    logging.info(f"🔐 Password reset requested for {request.email}")
    logging.info(f"📧 Reset link (copy to browser): {reset_link}")
    logging.info(f"⏰ Token expires at: {expires_at.isoformat()}")
    
    return {"success": True, "message": "Si el email existe, recibirás instrucciones", "reset_link": reset_link}

@api_router.post("/auth/password-reset/validate")
async def validate_reset_token(token: str):
    """Validate if reset token is valid"""
    reset_request = await db.password_resets.find_one({
        "token": token,
        "used": False
    })
    
    if not reset_request:
        raise HTTPException(status_code=400, detail="Token inválido o ya usado")
    
    # Check expiration
    expires_at = datetime.fromisoformat(reset_request["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Token expirado")
    
    return {"success": True, "email": reset_request["email"]}

@api_router.post("/auth/password-reset/confirm")
async def confirm_password_reset(reset: PasswordResetConfirm):
    """Reset password with valid token"""
    # Validate token
    reset_request = await db.password_resets.find_one({
        "token": reset.token,
        "used": False
    })
    
    if not reset_request:
        raise HTTPException(status_code=400, detail="Token inválido o ya usado")
    
    # Check expiration
    expires_at = datetime.fromisoformat(reset_request["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Token expirado")
    
    # Update password
    password_hash = hashlib.sha256(reset.new_password.encode()).hexdigest()
    await db.users.update_one(
        {"email": reset_request["email"]},
        {"$set": {"password_hash": password_hash}}
    )
    
    # Mark token as used
    await db.password_resets.update_one(
        {"token": reset.token},
        {"$set": {"used": True}}
    )
    
    logging.info(f"✅ Password reset completed for {reset_request['email']}")
    
    return {"success": True, "message": "Contraseña actualizada exitosamente"}

# ==================== ADMIN ENDPOINTS ====================

@api_router.post("/admin/login")
async def admin_login(credentials: AdminLogin, response: Response):
    """Admin login"""
    password_hash = hashlib.sha256(credentials.password.encode()).hexdigest()
    
    admin = await db.admins.find_one({
        "email": credentials.email,
        "password_hash": password_hash
    })
    
    if not admin:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    
    # Create admin session
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    
    await db.admin_sessions.insert_one({
        "admin_id": admin["id"],
        "session_token": session_token,
        "expires_at": expires_at.isoformat()
    })
    
    # Set cookie
    response.set_cookie(
        key="admin_session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=604800
    )
    
    return {"success": True, "admin": {"id": admin["id"], "email": admin["email"], "name": admin["name"]}}

async def get_admin_from_session(request: Request):
    """Verify admin session"""
    admin_session = request.cookies.get("admin_session")
    if not admin_session:
        raise HTTPException(status_code=401, detail="No autenticado")
    
    session = await db.admin_sessions.find_one({"session_token": admin_session})
    if not session:
        raise HTTPException(status_code=401, detail="Sesión inválida")
    
    # Check expiration
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=401, detail="Sesión expirada")
    
    admin = await db.admins.find_one({"id": session["admin_id"]}, {"_id": 0, "password_hash": 0})
    return admin

@api_router.get("/admin/stats")
async def get_admin_stats(request: Request):
    """Get application statistics"""
    await get_admin_from_session(request)
    
    total_users = await db.users.count_documents({})
    total_matches = await db.matches.count_documents({})
    total_messages = await db.messages.count_documents({})
    total_groups = await db.groups.count_documents({})
    
    # Users by gender
    male_users = await db.users.count_documents({"gender": "male"})
    female_users = await db.users.count_documents({"gender": "female"})
    other_users = await db.users.count_documents({"gender": "other"})
    
    return {
        "total_users": total_users,
        "total_matches": total_matches,
        "total_messages": total_messages,
        "total_groups": total_groups,
        "users_by_gender": {
            "male": male_users,
            "female": female_users,
            "other": other_users
        }
    }

@api_router.get("/admin/users")
async def get_all_users(
    request: Request,
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None
):
    """Get all users with pagination and search"""
    await get_admin_from_session(request)
    
    query = {}
    if search:
        query = {
            "$or": [
                {"name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}}
            ]
        }
    
    users = await db.users.find(query, {"_id": 0, "password_hash": 0}).skip(skip).limit(limit).to_list(length=None)
    total = await db.users.count_documents(query)
    
    return {
        "users": users,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@api_router.get("/admin/matches")
async def get_all_matches(request: Request, skip: int = 0, limit: int = 20):
    """Get all matches"""
    await get_admin_from_session(request)
    
    matches = await db.matches.find({}, {"_id": 0}).skip(skip).limit(limit).to_list(length=None)
    total = await db.matches.count_documents({})
    
    return {
        "matches": matches,
        "total": total,
        "skip": skip,
        "limit": limit
    }

# ==================== USER ENDPOINTS ====================

@api_router.get("/users/{user_id}")
async def get_user(user_id: str):
    """Get user by ID"""
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user

@api_router.put("/users/{user_id}")
async def update_user(user_id: str, updates: dict):
    """Update user profile"""
    await db.users.update_one(
        {"id": user_id},
        {"$set": updates}
    )
    return {"success": True}

@api_router.post("/users/{user_id}/upload-video")
async def upload_video(user_id: str, video_data: dict):
    """Upload verification video to ImageKit"""
    try:
        video_base64 = video_data.get("video_data")
        if not video_base64:
            raise HTTPException(status_code=400, detail="No video data provided")
        
        # Upload to ImageKit
        options = UploadFileRequestOptions(
            folder=f"/machat/users/{user_id}/videos/",
            use_unique_file_name=True,
            is_private_file=False,
            tags=["verification", "profile_video"]
        )
        
        upload_result = imagekit.upload_file(
            file=video_base64,
            file_name=f"{user_id}_verification.mp4",
            options=options
        )
        
        video_url = upload_result.response_metadata.raw['url']
        
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"video_url": video_url, "verified": True}}
        )
        
        return {"success": True, "video_url": video_url}
    except Exception as e:
        logging.error(f"Video upload error: {e}")
        raise HTTPException(status_code=500, detail="Error uploading video")

@api_router.post("/users/{user_id}/upload-photos")
async def upload_photos(user_id: str, photos_data: dict):
    """Upload photos to ImageKit and check for filters"""
    photos = photos_data.get("photos", [])
    validated_photos = []
    rejected_photos = []
    
    for i, photo_base64 in enumerate(photos):
        try:
            # Check for filters using Gemini Vision
            has_filter = await check_filter_in_photo(photo_base64)
            
            if has_filter:
                rejected_photos.append(i)
            else:
                # Upload to ImageKit
                options = UploadFileRequestOptions(
                    folder=f"/machat/users/{user_id}/photos/",
                    use_unique_file_name=True,
                    is_private_file=False,
                    tags=["profile_photo"],
                    transformation={
                        "pre": "w-2000,h-2000,q-80"
                    }
                )
                
                upload_result = imagekit.upload_file(
                    file=photo_base64,
                    file_name=f"{user_id}_photo_{i}.jpg",
                    options=options
                )
                validated_photos.append(upload_result.response_metadata.raw['url'])
        except Exception as e:
            logging.error(f"Photo upload error for photo {i}: {e}")
            rejected_photos.append(i)
    
    # Update user photos
    if validated_photos:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"photos": validated_photos}}
        )
    
    return {
        "success": True,
        "accepted_photos": len(validated_photos),
        "rejected_photos": len(rejected_photos),
        "rejected_indices": rejected_photos,
        "message": "Las fotos con filtro entran por los ojos, pero no en Machat." if rejected_photos else None
    }

@api_router.post("/users/{user_id}/location")
async def update_location(user_id: str, location: UserLocation):
    """Update user location"""
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"location": location.model_dump()}}
    )
    return {"success": True}

# ==================== MATCHING ENDPOINTS ====================

@api_router.get("/matches/potential/{user_id}")
async def get_potential_matches(user_id: str, match_type: str = "main", limit: int = 20):
    """Get potential matches based on preferences and location"""
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    prefs = user.get("preferences", {})
    user_location = user.get("location", {})
    blocked_users = user.get("blocked_users", [])
    
    # Find users already swiped for this match type
    existing_swipes = await db.swipes.find({
        "user_id": user_id,
        "match_type": match_type
    }, {"_id": 0}).to_list(1000)
    swiped_ids = [s["target_user_id"] for s in existing_swipes]
    
    # Combine excluded IDs (swiped + blocked)
    excluded_ids = list(set(swiped_ids + blocked_users))
    
    # Build query based on preferences
    query = {
        "id": {"$ne": user_id, "$nin": excluded_ids},
        "age": {"$gte": prefs.get("age_min", 18), "$lte": prefs.get("age_max", 99)},
        "profile_visible": {"$ne": False}  # Exclude hidden profiles
    }
    
    if prefs.get("gender_preference") != "both":
        query["gender"] = prefs.get("gender_preference")
    
    # Get potential matches
    potential = await db.users.find(query, {"_id": 0, "password_hash": 0}).limit(limit).to_list(limit)
    
    # Filter out users who have blocked current user
    filtered_potential = []
    for pot_user in potential:
        pot_blocked = pot_user.get("blocked_users", [])
        if user_id not in pot_blocked:
            filtered_potential.append(pot_user)
    
    return {"users": filtered_potential}

@api_router.post("/matches/swipe/{user_id}")
async def swipe_user(user_id: str, action: SwipeAction):
    """Swipe on a user (like/pass)"""
    # Record swipe
    swipe_data = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "target_user_id": action.target_user_id,
        "action": action.action,
        "match_type": action.match_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.swipes.insert_one(swipe_data)
    
    matched = False
    
    if action.action == "like":
        # Check if other user also liked in the same match type
        other_swipe = await db.swipes.find_one({
            "user_id": action.target_user_id,
            "target_user_id": user_id,
            "action": "like",
            "match_type": action.match_type
        })
        
        if other_swipe:
            # Create match!
            match = Match(
                user1_id=user_id, 
                user2_id=action.target_user_id, 
                match_type=action.match_type,
                status="matched"
            )
            await db.matches.insert_one(match.model_dump())
            
            # Create chat for the match
            chat_data = {
                "id": str(uuid.uuid4()),
                "match_id": match.id,
                "match_type": action.match_type,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await db.chats.insert_one(chat_data)
            
            # Send notifications
            await create_notification(user_id, "new_match", {
                "match_id": match.id, 
                "user_id": action.target_user_id,
                "match_type": action.match_type
            })
            await create_notification(action.target_user_id, "new_match", {
                "match_id": match.id, 
                "user_id": user_id,
                "match_type": action.match_type
            })
            
            matched = True
    
    return {"success": True, "matched": matched}

@api_router.get("/matches/list/{user_id}")
async def get_user_matches(user_id: str, match_type: str = None):
    """Get all matches for a user, optionally filtered by type"""
    query = {
        "$or": [{"user1_id": user_id}, {"user2_id": user_id}],
        "status": "matched"
    }
    
    if match_type:
        query["match_type"] = match_type
    
    matches = await db.matches.find(query, {"_id": 0}).to_list(1000)
    
    # Populate user info for each match
    for match in matches:
        other_id = match["user2_id"] if match["user1_id"] == user_id else match["user1_id"]
        other_user = await db.users.find_one({"id": other_id}, {"_id": 0, "password_hash": 0})
        match["other_user"] = other_user
    
    return {"matches": matches}

# ==================== CHAT ENDPOINTS ====================

@api_router.get("/chats/{match_id}/messages")
async def get_chat_messages(match_id: str):
    """Get messages for a chat"""
    chat = await db.chats.find_one({"match_id": match_id}, {"_id": 0})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")
    
    messages = await db.messages.find({"chat_id": chat["id"]}, {"_id": 0}).sort("timestamp", 1).to_list(1000)
    
    return {"messages": messages}

@api_router.post("/chats/{match_id}/messages")
async def send_message(match_id: str, sender_id: str, message: MessageCreate):
    """Send message in chat"""
    chat = await db.chats.find_one({"match_id": match_id}, {"_id": 0})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")
    
    msg = Message(chat_id=chat["id"], sender_id=sender_id, content=message.content)
    await db.messages.insert_one(msg.model_dump())
    
    # Get match to find receiver
    match = await db.matches.find_one({"id": match_id}, {"_id": 0})
    receiver_id = match["user2_id"] if match["user1_id"] == sender_id else match["user1_id"]
    
    # Send notification
    await create_notification(receiver_id, "new_message", {"match_id": match_id, "sender_id": sender_id})
    
    # Send via WebSocket
    await manager.send_personal_message({
        "type": "new_message",
        "data": msg.model_dump()
    }, receiver_id)
    
    return msg

# ==================== GROUP CHAT ENDPOINTS ====================

@api_router.post("/groups/create")
async def create_group(user_id: str, group_data: GroupChatCreate):
    """Create a new group meetup"""
    group = GroupChat(
        **group_data.model_dump(),
        created_by=user_id,
        members=[user_id]  # Creator automatically joins
    )
    await db.group_chats.insert_one(group.model_dump())
    
    return group

@api_router.get("/groups/list")
async def get_all_groups(
    location: Optional[str] = None,
    activity_type: Optional[str] = None,
    limit: int = 50
):
    """Get all public groups, optionally filtered by location and activity type"""
    query = {"is_public": True}
    
    if location:
        query["location"] = {"$regex": location, "$options": "i"}
    
    if activity_type and activity_type != "all":
        query["activity_type"] = activity_type
    
    groups = await db.group_chats.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    
    # Populate creator info and member count
    for group in groups:
        creator = await db.users.find_one({"id": group["created_by"]}, {"_id": 0, "password_hash": 0})
        group["creator"] = creator
        group["member_count"] = len(group.get("members", []))
    
    return {"groups": groups}

@api_router.get("/groups/my/{user_id}")
async def get_user_groups(user_id: str):
    """Get all groups where user is a member"""
    groups = await db.group_chats.find({"members": user_id}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    
    # Populate member count
    for group in groups:
        group["member_count"] = len(group.get("members", []))
        creator = await db.users.find_one({"id": group["created_by"]}, {"_id": 0, "password_hash": 0})
        group["creator"] = creator
    
    return {"groups": groups}

@api_router.post("/groups/{group_id}/join")
async def join_group(group_id: str, user_id: str):
    """Join a public group"""
    group = await db.group_chats.find_one({"id": group_id}, {"_id": 0})
    
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    
    if not group.get("is_public", True):
        raise HTTPException(status_code=403, detail="Este grupo es privado")
    
    if user_id in group.get("members", []):
        raise HTTPException(status_code=400, detail="Ya eres miembro de este grupo")
    
    if len(group.get("members", [])) >= group.get("max_members", 20):
        raise HTTPException(status_code=400, detail="El grupo está lleno")
    
    # Add user to group
    await db.group_chats.update_one(
        {"id": group_id},
        {"$addToSet": {"members": user_id}}
    )
    
    # Notify group members
    for member_id in group.get("members", []):
        if member_id != user_id:
            await create_notification(member_id, "group_member_joined", {
                "group_id": group_id,
                "group_name": group["name"],
                "user_id": user_id
            })
    
    return {"success": True}

@api_router.post("/groups/{group_id}/leave")
async def leave_group(group_id: str, user_id: str):
    """Leave a group"""
    await db.group_chats.update_one(
        {"id": group_id},
        {"$pull": {"members": user_id}}
    )
    
    return {"success": True}

@api_router.get("/groups/{group_id}")
async def get_group_details(group_id: str):
    """Get group details with members"""
    group = await db.group_chats.find_one({"id": group_id}, {"_id": 0})
    
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    
    # Populate members
    members = []
    for member_id in group.get("members", []):
        member = await db.users.find_one({"id": member_id}, {"_id": 0, "password_hash": 0})
        if member:
            members.append(member)
    
    group["members_details"] = members
    group["member_count"] = len(members)
    
    # Populate creator
    creator = await db.users.find_one({"id": group["created_by"]}, {"_id": 0, "password_hash": 0})
    group["creator"] = creator
    
    return group

@api_router.get("/groups/{group_id}/messages")
async def get_group_messages(group_id: str):
    """Get messages in a group"""
    messages = await db.group_messages.find({"group_id": group_id}, {"_id": 0}).sort("timestamp", 1).to_list(1000)
    return {"messages": messages}

@api_router.post("/groups/{group_id}/messages")
async def send_group_message(group_id: str, sender_id: str, message: MessageCreate):
    """Send message in group"""
    group = await db.group_chats.find_one({"id": group_id}, {"_id": 0})
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    
    msg = Message(chat_id=group_id, sender_id=sender_id, content=message.content)
    msg_dict = msg.model_dump()
    msg_dict["group_id"] = group_id
    await db.group_messages.insert_one(msg_dict)
    
    # Notify all members except sender
    for member_id in group["members"]:
        if member_id != sender_id:
            await create_notification(member_id, "new_group_message", {"group_id": group_id, "sender_id": sender_id})
    
    return msg

# ==================== DATE SAFETY ENDPOINTS ====================

@api_router.post("/dates/confirm/{match_id}")
async def confirm_date(match_id: str, user_id: str):
    """Confirm going on a date"""
    # Get or create date meeting
    date = await db.date_meetings.find_one({"match_id": match_id}, {"_id": 0})
    
    match = await db.matches.find_one({"id": match_id}, {"_id": 0})
    if not match:
        raise HTTPException(status_code=404, detail="Match no encontrado")
    
    if not date:
        date = DateMeeting(match_id=match_id)
        date_dict = date.model_dump()
    else:
        date_dict = date
    
    # Update confirmation
    if user_id == match["user1_id"]:
        date_dict["user1_confirmed"] = True
    else:
        date_dict["user2_confirmed"] = True
    
    # Check if both confirmed
    if date_dict["user1_confirmed"] and date_dict["user2_confirmed"]:
        date_dict["status"] = "confirmed"
    
    await db.date_meetings.update_one(
        {"match_id": match_id},
        {"$set": date_dict},
        upsert=True
    )
    
    # Notify other user
    other_id = match["user2_id"] if user_id == match["user1_id"] else match["user1_id"]
    await create_notification(other_id, "date_confirmed", {"match_id": match_id, "user_id": user_id})
    
    return {"success": True, "date": date_dict}

@api_router.post("/dates/arrive/{match_id}")
async def arrive_at_date(match_id: str, user_id: str):
    """Mark arrival at date location"""
    date = await db.date_meetings.find_one({"match_id": match_id}, {"_id": 0})
    if not date:
        raise HTTPException(status_code=404, detail="Cita no encontrada")
    
    match = await db.matches.find_one({"id": match_id}, {"_id": 0})
    
    # Update arrival
    if user_id == match["user1_id"]:
        date["user1_arrived"] = True
    else:
        date["user2_arrived"] = True
    
    if date["user1_arrived"] and date["user2_arrived"]:
        date["status"] = "in_progress"
    
    await db.date_meetings.update_one(
        {"match_id": match_id},
        {"$set": date}
    )
    
    # Notify other user
    other_id = match["user2_id"] if user_id == match["user1_id"] else match["user1_id"]
    await create_notification(other_id, "date_arrival", {"match_id": match_id, "user_id": user_id})
    
    return {"success": True}

@api_router.post("/dates/{match_id}/rate")
async def rate_date(match_id: str, rater_id: str, rating_data: RatingCreate):
    """Rate a user after a date"""
    # Verify date was confirmed
    date = await db.date_meetings.find_one({"match_id": match_id}, {"_id": 0})
    if not date or date["status"] not in ["confirmed", "in_progress", "completed"]:
        raise HTTPException(status_code=400, detail="Solo puedes calificar después de confirmar la cita")
    
    # Get match to find rated user
    match = await db.matches.find_one({"id": match_id}, {"_id": 0})
    rated_user_id = match["user2_id"] if match["user1_id"] == rater_id else match["user1_id"]
    
    # Get rated user's phone
    rated_user = await db.users.find_one({"id": rated_user_id}, {"_id": 0})
    
    # Create rating
    rating = Rating(
        rated_user_phone=rated_user["phone"],
        rater_id=rater_id,
        stars=rating_data.stars,
        tags=rating_data.tags,
        date_id=rating_data.date_id
    )
    await db.ratings.insert_one(rating.model_dump())
    
    # Update user's average rating
    all_ratings = await db.ratings.find({"rated_user_phone": rated_user["phone"]}, {"_id": 0}).to_list(1000)
    avg_rating = sum(r["stars"] for r in all_ratings) / len(all_ratings)
    
    await db.users.update_one(
        {"phone": rated_user["phone"]},
        {"$set": {"rating_average": avg_rating, "rating_count": len(all_ratings)}}
    )
    
    # Mark date as completed
    await db.date_meetings.update_one(
        {"match_id": match_id},
        {"$set": {"status": "completed"}}
    )
    
    # Notify rated user
    await create_notification(rated_user_id, "new_rating", {"stars": rating_data.stars})
    
    return {"success": True}

# ==================== NOTIFICATION ENDPOINTS ====================

@api_router.get("/notifications/{user_id}")
async def get_notifications(user_id: str):
    """Get user notifications"""
    notifs = await db.notifications.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(50).to_list(50)
    return {"notifications": notifs}

@api_router.post("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    """Mark notification as read"""
    await db.notifications.update_one(
        {"id": notif_id},
        {"$set": {"read": True}}
    )
    return {"success": True}

# ==================== PRIVACY ENDPOINTS ====================

@api_router.post("/users/{user_id}/toggle-visibility")
async def toggle_profile_visibility(user_id: str):
    """Toggle profile visibility (hide/show profile)"""
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    new_visibility = not user.get("profile_visible", True)
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"profile_visible": new_visibility}}
    )
    
    return {
        "success": True,
        "profile_visible": new_visibility,
        "message": "Perfil oculto" if not new_visibility else "Perfil visible"
    }

@api_router.post("/users/{user_id}/block/{target_user_id}")
async def block_user(user_id: str, target_user_id: str):
    """Block a user"""
    if user_id == target_user_id:
        raise HTTPException(status_code=400, detail="No puedes bloquearte a ti mismo")
    
    # Add to blocked list
    await db.users.update_one(
        {"id": user_id},
        {"$addToSet": {"blocked_users": target_user_id}}
    )
    
    # Remove any existing matches
    await db.matches.update_many(
        {
            "$or": [
                {"user1_id": user_id, "user2_id": target_user_id},
                {"user1_id": target_user_id, "user2_id": user_id}
            ]
        },
        {"$set": {"status": "blocked"}}
    )
    
    return {"success": True, "message": "Usuario bloqueado"}

@api_router.post("/users/{user_id}/unblock/{target_user_id}")
async def unblock_user(user_id: str, target_user_id: str):
    """Unblock a user"""
    await db.users.update_one(
        {"id": user_id},
        {"$pull": {"blocked_users": target_user_id}}
    )
    
    return {"success": True, "message": "Usuario desbloqueado"}

@api_router.get("/users/{user_id}/blocked")
async def get_blocked_users(user_id: str):
    """Get list of blocked users"""
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    blocked_ids = user.get("blocked_users", [])
    blocked_users = []
    
    for blocked_id in blocked_ids:
        blocked_user = await db.users.find_one({"id": blocked_id}, {"_id": 0, "password_hash": 0})
        if blocked_user:
            blocked_users.append(blocked_user)
    
    return {"blocked_users": blocked_users}

# ==================== WEBSOCKET ====================

@app.websocket("/ws/chat/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle incoming messages if needed
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)

# ==================== ROOT ====================

@api_router.get("/")
async def root():
    return {"message": "Machat API - Donde las malas citas hacen logout 💕"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()