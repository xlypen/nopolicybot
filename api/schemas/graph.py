# Source Generated with Decompyle++
# File: graph.cpython-312.pyc (Python 3.12)

from pydantic import BaseModel, ConfigDict, Field

class GraphNode(BaseModel):
    id: int = 'GraphNode'
    label: str | None = None
    avatar: str | None = None
    model_config = ConfigDict(extra = 'allow')


class GraphEdge(BaseModel):
    target: int = 'GraphEdge'
    weight: float | int = 0
    model_config = ConfigDict(extra = 'allow')


class GraphMeta(BaseModel):
    period: str = 'GraphMeta'
    nodes_count: int = Field(ge = 0, default = 0)
    edges_count: int = Field(ge = 0, default = 0)
    model_config = ConfigDict(extra = 'allow')


class GraphPayload(BaseModel):
    meta: GraphMeta = 'GraphPayload'


class GraphResponse(BaseModel):
    graph: GraphPayload = True

