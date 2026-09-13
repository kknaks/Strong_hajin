"""Personal preferences are ordinary user commands, separate from administrative grants."""
from typing import Literal, get_args
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


AssistantCharacterKey = Literal['cream-cat', 'silver-tabby', 'tuxedo-cat', 'calico-cat', 'puppy', 'rabbit', 'bear', 'chick', 'red-panda']
ASSISTANT_CHARACTER_LABELS = {'cream-cat': '크림 고양이', 'silver-tabby': '실버 태비', 'tuxedo-cat': '턱시도', 'calico-cat': '삼색 고양이', 'puppy': '강아지', 'rabbit': '토끼', 'bear': '곰', 'chick': '병아리', 'red-panda': '레서판다'}
ASSISTANT_CHARACTER_KEYS = frozenset(get_args(AssistantCharacterKey))


class AssistantCharacterInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    character_key: AssistantCharacterKey = Field( title='캐릭터')
    expected_version: int = Field(ge=0, title='설정 버전')


class AssistantCharacterResult(TypedDict):
    character_key: str
    version: int
