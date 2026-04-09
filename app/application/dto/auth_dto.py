from pydantic import BaseModel, EmailStr


class LoginRequestDTO(BaseModel):
    email: EmailStr
    password: str

class LoginResponseDTO(BaseModel):
    user_id: str
    email: EmailStr
    access_token: str
