from pydantic import BaseModel, ConfigDict, Field


class GraphNode(BaseModel):
    id: int
    label: str | None = None
    avatar: str | None = None
    model_config = ConfigDict(extra="allow")


class GraphEdge(BaseModel):
    target: int
    weight: float | int = 0
    model_config = ConfigDict(extra="allow")


class GraphMeta(BaseModel):
    period: str = ""
    nodes_count: int = Field(ge=0, default=0)
    edges_count: int = Field(ge=0, default=0)
    model_config = ConfigDict(extra="allow")


class GraphPayload(BaseModel):
    meta: GraphMeta = Field(default_factory=GraphMeta)


class GraphResponse(BaseModel):
    graph: GraphPayload = Field(default_factory=GraphPayload)
