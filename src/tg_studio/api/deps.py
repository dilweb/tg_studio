from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
