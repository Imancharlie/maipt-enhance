from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # API
    API_KEY: str = "your-shared-api-key-here"
    
    # Anthropic Claude
    ANTHROPIC_API_KEY: str
    
    # AI Settings
    CLAUDE_MODEL: str = "claude-3-haiku-20240307"
    # High enough for a full report (5 days + operations) in one response;
    # a cut-off reply can never be parsed, so never leave this low.
    MAX_TOKENS: int = 8000
    TEMPERATURE: float = 0.7
    
    # Backend
    BACKEND_URL: str = "http://localhost:8000"
    
    # Session Settings
    SESSION_TTL_MINUTES: int = 120  # 2 hours
    
    # Analytics
    ENABLE_ANONYMIZED_LOGGING: bool = True
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
