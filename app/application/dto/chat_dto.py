from typing import Optional
from pydantic import BaseModel


class SendChatMessageRequestDTO(BaseModel):
    user_id: str
    message: str
    conversation_id: Optional[str] = None

class SendChatMessageResponseDTO(BaseModel):
    conversation_id: str
    answer: str
    model: str
