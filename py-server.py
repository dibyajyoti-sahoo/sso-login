from __future__ import annotations

import base64
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import uvicorn

load_dotenv()

SUPERTOKENS_BASE = os.environ.get("SUPERTOKENS_BASE", "http://supertokens:3567")
API_KEY = os.environ.get("SUPERTOKENS_API_KEY", "")
CDI_VERSION = os.environ.get("CDI_VERSION", "4.0")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
PYHOST = os.environ.get("PYHOST")
PYPORT = os.environ.get("PYPORT")

_client = httpx.AsyncClient(base_url=SUPERTOKENS_BASE, timeout=10.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not API_KEY:
        print("WARNING: SUPERTOKENS_API_KEY is empty. Core requests will be rejected.")
    yield
    await _client.aclose()


app = FastAPI(
    title="Auth Server API",
    version="1.0.0",
    description=(
        "Public authentication API backed by SuperTokens Core.\n\n"
        "- `signup` / `signin` use HTTP Basic: the user's email (username) and "
        "password.\n"
        "- `verify` / `signout` / `delete` take the user's **access token** as a "
        "Bearer token.\n"
        "- `refresh` takes the user's **refresh token** as a Bearer token."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "health", "description": "Connectivity checks."},
        {"name": "auth", "description": "Authentication and session lifecycle."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security schemes -- these are what render the "Authorize" dialog in Swagger UI.
basic_scheme = HTTPBasic(description='base64("email:password") in the Authorization header.')
bearer_scheme = HTTPBearer(
    description="A SuperTokens token. Use the access token for verify/signout/delete, "
    "and the refresh token for refresh.",
)


# --------------------------------------------------------------------------- #
# Response models (drive the schemas shown in /docs)
# --------------------------------------------------------------------------- #
class LoginMethod(BaseModel):
    tenantIds: list[str] = []
    recipeUserId: str | None = None
    verified: bool | None = None
    timeJoined: int | None = Field(default=None, description="Epoch milliseconds")
    recipeId: str | None = Field(default=None, examples=["emailpassword"])
    email: str | None = None


class User(BaseModel):
    id: str
    isPrimaryUser: bool | None = None
    tenantIds: list[str] = Field(default_factory=list, examples=[["public"]])
    timeJoined: int | None = Field(default=None, description="Epoch milliseconds")
    emails: list[str] = []
    phoneNumbers: list[str] = []
    thirdParty: list[dict[str, Any]] = []
    loginMethods: list[LoginMethod] = []


class UserResponse(BaseModel):
    status: str = Field(examples=["OK", "EMAIL_ALREADY_EXISTS_ERROR"])
    user: User | None = None
    recipeUserId: str | None = None


class Token(BaseModel):
    token: str
    expiry: int = Field(description="Epoch milliseconds")
    createdTime: int = Field(description="Epoch milliseconds")


class Session(BaseModel):
    handle: str
    userId: str
    recipeUserId: str | None = None
    userDataInJWT: dict[str, Any] = {}
    tenantId: str | None = Field(default=None, examples=["public"])


class SessionResponse(BaseModel):
    status: str = Field(examples=["OK", "WRONG_CREDENTIALS_ERROR"])
    session: Session | None = None
    accessToken: Token | None = None
    refreshToken: Token | None = None


class VerifyResponse(BaseModel):
    valid: bool


class SignoutResponse(BaseModel):
    status: str = Field(examples=["OK"])
    sessionHandlesRevoked: list[str] = []


class StatusResponse(BaseModel):
    status: str = Field(examples=["OK"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _core_headers(rid: str | None = None) -> dict[str, str]:
    headers = {
        "api-key": API_KEY,
        "cdi-version": CDI_VERSION,
        "Content-Type": "application/json",
    }
    if rid is not None:
        headers["rid"] = rid
    return headers


def _forward(r: httpx.Response) -> Response:
    """Pass Core's status code and JSON body straight back to the caller.

    Returning a Response object means FastAPI does NOT re-validate against the
    declared response_model, so error payloads (e.g. EMAIL_ALREADY_EXISTS_ERROR)
    forward cleanly while the success schema still documents /docs.
    """
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


def _user_id_from_token(jwt: str) -> str:
    """Read the 'sub' claim from a JWT payload (no signature verification here)."""
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


async def _access_token_valid(token: str) -> bool:
    r = await _client.post(
        "/recipe/session/verify",
        headers=_core_headers(rid="session"),
        json={
            "accessToken": token,
            "enableAntiCsrf": False,
            "doAntiCsrfCheck": False,
            "checkDatabase": False,
            "antiCsrfToken": token,
        },
    )
    return r.status_code == 200 and r.json().get("status") == "OK"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", tags=["health"], summary="Health check")
async def root() -> Response:
    """Proxy Core's root endpoint. Returns 'Hello' when Core is reachable."""
    r = await _client.get("/")
    return Response(content=r.content, status_code=r.status_code, media_type="text/plain")


@app.post(
    "/auth/signup",
    tags=["auth"],
    summary="Register a new user",
    response_model=UserResponse,
)
async def signup(creds: HTTPBasicCredentials = Depends(basic_scheme)) -> Response:
    r = await _client.post(
        "/recipe/signup",
        headers=_core_headers(rid="emailpassword"),
        json={"email": creds.username, "password": creds.password},
    )
    return _forward(r)


@app.post(
    "/auth/signin",
    tags=["auth"],
    summary="Authenticate and start a session",
    response_model=SessionResponse,
)
async def signin(creds: HTTPBasicCredentials = Depends(basic_scheme)) -> Response:
    signin_resp = await _client.post(
        "/recipe/signin",
        headers=_core_headers(rid="emailpassword"),
        json={"email": creds.username, "password": creds.password},
    )
    try:
        body = signin_resp.json()
    except ValueError:
        # Core returned non-JSON (e.g. "Invalid API key"). Forward it as-is
        # instead of crashing on .json().
        return Response(
            content=signin_resp.content,
            status_code=signin_resp.status_code if signin_resp.status_code >= 400 else 502,
            media_type="text/plain",
        )
    if body.get("status") != "OK":
        return _forward(signin_resp)  # wrong credentials / unknown user

    session_resp = await _client.post(
        "/recipe/session",
        headers=_core_headers(rid="session"),
        json={
            "userId": body["user"]["id"],
            "userDataInJWT": {},        # add any access-token claims here
            "userDataInDatabase": {},
            "enableAntiCsrf": False,
        },
    )
    return _forward(session_resp)


@app.post(
    "/auth/verify",
    tags=["auth"],
    summary="Verify an access token",
    response_model=VerifyResponse,
    responses={401: {"model": VerifyResponse, "description": "Token invalid or expired"}},
)
async def verify(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Response:
    if await _access_token_valid(creds.credentials):
        return JSONResponse({"valid": True}, status_code=200)
    return JSONResponse({"valid": False}, status_code=401)


@app.post(
    "/auth/refresh",
    tags=["auth"],
    summary="Refresh an expired session",
    response_model=SessionResponse,
    description="Pass the **refresh** token (not the access token) as the Bearer token.",
)
async def refresh(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Response:
    r = await _client.post(
        "/recipe/session/refresh",
        headers=_core_headers(rid="session"),
        json={
            "refreshToken": creds.credentials,
            "enableAntiCsrf": False,
            "antiCsrfToken": creds.credentials,
        },
    )
    return _forward(r)


@app.post(
    "/auth/signout",
    tags=["auth"],
    summary="Sign out (revoke sessions)",
    response_model=SignoutResponse,
    responses={401: {"model": VerifyResponse, "description": "Access token invalid"}},
)
async def signout(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Response:
    if not await _access_token_valid(creds.credentials):
        return JSONResponse({"valid": False}, status_code=401)

    user_id = _user_id_from_token(creds.credentials)
    r = await _client.post(
        "/recipe/session/remove",
        headers=_core_headers(rid="session"),
        json={"userId": user_id},
    )
    return _forward(r)


@app.post(
    "/auth/delete",
    tags=["auth"],
    summary="Delete the user (irreversible)",
    response_model=StatusResponse,
    responses={401: {"model": VerifyResponse, "description": "Access token invalid"}},
)
async def delete_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Response:
    if not await _access_token_valid(creds.credentials):
        return JSONResponse({"valid": False}, status_code=401)

    user_id = _user_id_from_token(creds.credentials)
    r = await _client.post(
        "/user/remove",
        headers=_core_headers(),  # Core operation, no rid
        json={"userId": user_id},
    )
    return _forward(r)

if __name__ == "__main__":
    uvicorn.run(app, host=PYHOST, port=int(PYPORT))