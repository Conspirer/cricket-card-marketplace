from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime


class UserResponse(BaseModel):
    id: int
    username: str
    email: str

class UserCreate(BaseModel):
    username: str
    email: str

class PlayerCreate(BaseModel):
    name:str
    country:str
    role:str

class PlayerResponse(BaseModel):
    id:int
    name:str
    country:str
    role:str

class CardDefinitionCreate(BaseModel):
    player_id:int
    rarity:str
    max_supply:int

class CardDefinitionResponse(BaseModel):
    id:int
    player_id:int
    rarity:str
    max_supply:int

class CardInstanceCreate(BaseModel):
    card_definition_id: int
    serial_number: int
    owner_id: int


class CardInstanceResponse(BaseModel):
    id: int
    card_definition_id: int
    serial_number: int
    owner_id: int

class CardInstanceDetailResponse(BaseModel):
    id: int
    serial_number: int
    rarity: str
    player_name: str
    owner_username: str

class ListingCreate(BaseModel):
    card_instance_id: int
    seller_id: int
    price: Decimal

class ListingResponse(BaseModel):
    id: int
    card_instance_id: int
    seller_id: int
    price: Decimal
    status: str
    created_at: datetime
    resolved_at: datetime | None