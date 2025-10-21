"""数据库连接和会话管理"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.models.models import Base

# 处理PostgreSQL驱动选择
# 优先使用 psycopg (v3) 以支持 Serverless 环境
database_url = settings.database_url
if database_url.startswith("postgresql://"):
    # 尝试使用 psycopg (v3) 驱动，纯Python实现，适合Serverless
    try:
        import psycopg  # noqa
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        print("[OK] Using psycopg (v3) driver for PostgreSQL")
    except ImportError:
        # 回退到 psycopg2-binary（本地开发）
        try:
            import psycopg2  # noqa
            print("[OK] Using psycopg2 driver for PostgreSQL")
        except ImportError:
            print("[WARNING] No PostgreSQL driver found. Please install psycopg or psycopg2-binary")

# 创建数据库引擎
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if "sqlite" in database_url else {}
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库（创建所有表）"""
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Database tables initialized successfully")
    except Exception as e:
        print(f"[ERROR] Failed to initialize database: {str(e)}")
        raise


def get_db() -> Session:
    """获取数据库会话（依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
