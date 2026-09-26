from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Literal
from datetime import date, datetime


class UserResponse(BaseModel):
    id: int
    username: str
    balance: Decimal

class PlayerCreate(BaseModel):
    name:str
    country:str
    role:str

class PlayerResponse(BaseModel):
    id:int
    name:str
    country:str
    role:str

class PlayerDetailResponse(PlayerResponse):
    batting_hand: str | None
    bowling_type: str | None
    is_keeper: bool | None

class ThemeStatsRow(BaseModel):
    theme: str
    label: str
    tier: str
    matches: int
    stats: dict[str, float | int | None] | None   # None when unverified
    source: str
    verified: bool
    as_of: date | None
    notes: str | None

class PlayerThemeStatsResponse(BaseModel):
    hidden: bool                 # True while the player is in an active battle
    themes: list[ThemeStatsRow]

class CardDefinitionCreate(BaseModel):
    player_id:int
    rarity:Literal["Common", "Rare", "Epic", "Legendary"]
    max_supply:int = Field(gt=0)
    is_active:bool = False

class CardDefinitionResponse(BaseModel):
    id:int
    player_id:int
    rarity:str
    max_supply:int
    minted_count:int
    is_active:bool

class CardInstanceCreate(BaseModel):
    card_definition_id: int
    owner_id: int


class CardInstanceResponse(BaseModel):
    id: int
    card_definition_id: int
    serial_number: int
    owner_id: int

class CardInstanceDetailResponse(BaseModel):
    id: int
    card_definition_id: int
    serial_number: int
    rarity: str
    player_name: str
    player_role: str
    player_country: str
    max_supply: int
    owner_username: str
    player_id: int

class ListingCreate(BaseModel):
    card_instance_id: int
    seller_id: int
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

class ListingResponse(BaseModel):
    id: int
    card_instance_id: int
    seller_id: int
    price: Decimal
    status: str
    created_at: datetime
    resolved_at: datetime | None

class GrantCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class PackOpenRequest(BaseModel):
    user_id: int
    pack_type: Literal["standard", "premium"]

class PackOpenResponse(BaseModel):
    pack_opening_id: int
    pack_type: str
    price: Decimal
    balance: Decimal
    cards: list[CardInstanceDetailResponse]

class CollectionCardResponse(CardInstanceDetailResponse):
    player_tier: str
    credits: int
    active_listing_id: int | None
    listed_price: Decimal | None

class MarketplaceListingResponse(BaseModel):
    listing_id: int
    card_instance_id: int
    card_definition_id: int
    serial_number: int
    rarity: str
    player_name: str
    player_role: str
    player_country: str
    max_supply: int
    player_id: int
    seller_id: int
    seller_username: str
    price: Decimal
    listed_at: datetime

class CardEventResponse(BaseModel):
    event_type: str
    from_username: str | None
    to_username: str | None
    price: Decimal | None
    related_listing_id: int | None
    related_pack_opening_id: int | None
    created_at: datetime

class SaleResponse(BaseModel):
    card_instance_id: int
    serial_number: int
    price: Decimal
    sold_at: datetime
