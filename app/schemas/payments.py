from pydantic import BaseModel


class WebhookResponse(BaseModel):
    received: bool = True
