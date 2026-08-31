from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.applications.models import (
    Application,
    ApplicationRequestedAction,
)
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)
from app.modules.compute.models import Artifact, ComputeJob
from app.modules.contracts.models import Contract, ContractParty, ContractRevision
from app.modules.dataset_model_evidence.models import (
    DatasetModelEvidence,
    DatasetModelRelation,
)
from app.modules.data_services.projection import resolve_data_service_capability
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ExternalCatalogSource,
    ExternalDatasetRecord,
    ExternalModelRecord,
    ModelProductExternalSourceLink,
)
from app.modules.identity.models import Organization
from app.modules.lifecycle.models import ProductLifecycleRequest
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import (
    SERVICE_MODE_LABELS,
    resolve_service_modes,
)
from app.modules.role_assistant.planner import PublicRole, ResourceKind
from app.modules.role_assistant.schemas import (
    AssistantCompatibilityEvidence,
    AssistantExecutionLineage,
    AssistantLineageNode,
    AssistantResource,
)
from app.modules.service_access.models import ServiceAccessRequest


MAX_RESULTS = 20
MAX_SEARCH_TERMS = 12
MAX_SEARCH_TERM_LENGTH = 80
_COUNT_PATTERN = re.compile(
    r"(?:有|共|一共|总共)?多少(?:个|项|条|份|例)?|几(?:个|项|条|份|例)|数量|总数|合计"
)
_PUBLIC_PATTERN = re.compile(r"公共|公开|开放|候选")
_PUBLISHED_PATTERN = re.compile(r"已发布|已上架|正式发布")
_PENDING_PATTERN = re.compile(
    r"待审|待办|待处理|未处理|待上架|待发布|待下架|待归档|"
    r"(?:数据|模型)(?:产品)?.{0,8}(?:审核|审批)|"
    r"(?:审核|审批).{0,8}(?:数据|模型)(?:产品)?|pending",
    re.I,
)
_AUTHORIZATION_REQUEST_PATTERN = re.compile(r"授权|许可|交付|商城", re.I)
_COMPUTE_REQUEST_PATTERN = re.compile(r"受控|计算|运行|验证", re.I)
_STOP_PATTERN = re.compile(
    r"帮我|请问|请|查找|搜索|找一下|看看|查看|定位|当前|现在|网站上面|网站上|平台里|平台|"
    r"(?:当前|本)?空间(?:里面|里|中|内)?|这里|里面|我的|我|目前|已经|同步了?|"
    r"我的|相关|有关|某个|一下|有多少|多少|几个|数量|总数|合计|"
    r"公共|公开|开放|候选|已发布|已上架|上架|数据产品|数据集|数据目录|数据库|"
    r"资料库|公共库|数据资源|数据资产|目录记录|条目|记录|数据|"
    r"模型产品|模型目录|模型|算法|申请条件|申请|需求|审批|待办|合约|合同|执行|任务|"
    r"就绪|进度|结果|下载|生命周期|链路|流程|兼容性|状态|"
    r"适配|证据矩阵|适配证据|证据链|血缘|产品详情|版本详情|详情|展示|"
    r"医院|本机构|参与|全过程|追溯|第三方|服务|接口|API|健康|可用|可以|"
    r"授权|许可|交付|服务方式|说明|哪些|哪一些|它们|他们|这些|那些|分别|"
    r"到哪一步了?|到什么阶段|进行到哪|进展如何"
)
_TERM_ALIASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"鼻咽癌|鼻咽肿瘤", re.I), ("鼻咽癌", "nasopharyngeal", "nasopharynx")),
    (re.compile(r"结直肠|肠癌", re.I), ("结直肠", "colorectal", "colon")),
    (re.compile(r"骨折|骨科", re.I), ("骨折", "fracture", "orthopedic", "orthopaedic")),
    (re.compile(r"病理|组织学", re.I), ("病理", "pathology", "histopathology", "histology")),
    (re.compile(r"磁共振|核磁|\bMRI?\b", re.I), ("MR", "MRI", "magnetic resonance")),
    (re.compile(r"CT|计算机断层", re.I), ("CT", "computed tomography")),
)


@dataclass(frozen=True)
class AssistantQueryContext:
    role: PublicRole
    space_id: Any
    actor: DemoActor
    session: AsyncSession


@dataclass(frozen=True)
class AssistantQueryResult:
    label: str
    unit: str
    source: str
    total: int
    items: list[AssistantResource]
    compatibility_evidence: list[AssistantCompatibilityEvidence] = field(
        default_factory=list
    )
    lineage: list[AssistantExecutionLineage] = field(default_factory=list)


def is_count_question(query: str) -> bool:
    return bool(_COUNT_PATTERN.search(query))


def _product_search_scope(
    *,
    role: PublicRole,
    provider_role: Literal["data_provider", "model_provider"],
    query: str,
) -> Literal["published", "pending", "owned"]:
    if role == "space_operator":
        return "pending" if _PENDING_PATTERN.search(query) else "published"
    if role != provider_role or _PUBLISHED_PATTERN.search(query):
        return "published"
    return "owned"


def _terms(query: str) -> list[str]:
    values: list[str] = []
    for pattern, aliases in _TERM_ALIASES:
        if pattern.search(query):
            values.extend(aliases)
    cleaned = _STOP_PATTERN.sub(" ", query.casefold())
    cleaned = re.sub(r"[与和的，。,:：;；/()（）？！?]+", " ", cleaned)
    values.extend(part.strip() for part in cleaned.split() if len(part.strip()) > 1)
    normalized = (
        value.casefold().strip()[:MAX_SEARCH_TERM_LENGTH]
        for value in values
        if value.strip()
    )
    return list(dict.fromkeys(normalized))[:MAX_SEARCH_TERMS]


def _compact_reference(value: str) -> str:
    return re.sub(r"[\s()（）·,，:：;；/\\._\-]+", "", value.casefold())


def _explicit_product_references(
    items: list[AssistantResource],
    query_text: str,
) -> list[AssistantResource]:
    query_value = _compact_reference(query_text)
    return [
        item
        for item in items
        if (title := _compact_reference(item.title)) and title in query_value
    ]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_clause(terms: list[str], *columns: Any) -> Any | None:
    if not terms:
        return None
    return or_(
        *(
            cast(column, Text).ilike(f"%{_escape_like(term)}%", escape="\\")
            for term in terms
            for column in columns
        )
    )


async def _total(session: AsyncSession, query: Any) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        or 0
    )


def _product_path(
    role: PublicRole,
    kind: ResourceKind,
    version_id: Any,
    *,
    provider_organization_id: Any,
    actor_organization_id: Any,
) -> str:
    if (
        kind == "data"
        and role == "data_provider"
        and provider_organization_id != actor_organization_id
    ):
        return "/data-catalog"
    if (
        kind == "model"
        and role == "model_provider"
        and provider_organization_id != actor_organization_id
    ):
        return "/model-catalog"
    return f"/{kind}-products/{version_id}"


def _service_mode_summary(
    kind: Literal["data", "model"],
    policy: dict[str, Any],
    *,
    published: bool,
    external: bool,
) -> str:
    if not published or external:
        return "仅目录元数据" if external else "尚未发布"
    modes = resolve_service_modes(kind, policy)
    return "、".join(SERVICE_MODE_LABELS[mode] for mode in modes)


async def search_data_products(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    if _PUBLIC_PATTERN.search(query_text):
        return await _search_public_datasets(context, query_text)

    session = context.session
    terms = _terms(query_text)
    statement = (
        select(
            DataProduct,
            DataProductVersion,
            Organization,
            DataProductExternalSourceLink.id,
        )
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .join(Organization, Organization.id == DataProduct.provider_organization_id)
        .outerjoin(
            DataProductExternalSourceLink,
            DataProductExternalSourceLink.data_product_version_id == DataProductVersion.id,
        )
        .where(DataProduct.space_id == context.space_id)
    )
    scope = _product_search_scope(
        role=context.role,
        provider_role="data_provider",
        query=query_text,
    )
    published = scope == "published"
    if published:
        statement = statement.join(
            DataProductPublication,
            DataProductPublication.data_product_version_id == DataProductVersion.id,
        ).where(DataProductPublication.status == "active")
    elif scope == "owned":
        statement = statement.where(
            DataProduct.provider_organization_id == context.actor.organization_id
        )
    elif scope == "pending":
        statement = statement.where(DataProductVersion.status == "under_review")
    clause = _search_clause(
        terms,
        DataProduct.product_code,
        DataProduct.name,
        DataProduct.description,
        DataProduct.domain,
        DataProductVersion.content_summary,
        DataProductVersion.linkage_metadata,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(session, statement)
    rows = (
        await session.execute(
            statement.order_by(DataProduct.updated_at.desc(), DataProductVersion.version_no.desc())
            .limit(MAX_RESULTS)
        )
    ).all()
    label = (
        "已发布数据产品"
        if scope == "published"
        else "待审核数据产品"
        if scope == "pending"
        else "数据产品"
    )
    items = [
        AssistantResource(
            key=f"data:{version.id}",
            kind="data",
            label=label,
            title=product.name,
            subtitle=" · ".join(
                value
                for value in (
                    provider.display_name,
                    product.domain,
                    str(version.linkage_metadata.get("modality") or ""),
                    _service_mode_summary(
                        "data",
                        version.default_policy_template,
                        published=published,
                        external=source_link_id is not None,
                    ),
                )
                if value
            ),
            status=version.status,
            path=_product_path(
                context.role,
                "data",
                version.id,
                provider_organization_id=product.provider_organization_id,
                actor_organization_id=context.actor.organization_id,
            ),
        )
        for product, version, provider, source_link_id in rows
    ]
    return AssistantQueryResult(
        label=label,
        unit="项",
        source="medtrust.data_products",
        total=total,
        items=items,
    )


async def search_data_services(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = (
        select(
            DataProduct,
            DataProductVersion,
            Organization,
            DataProductExternalSourceLink,
        )
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .join(Organization, Organization.id == DataProduct.provider_organization_id)
        .join(
            DataProductPublication,
            DataProductPublication.data_product_version_id == DataProductVersion.id,
        )
        .outerjoin(
            DataProductExternalSourceLink,
            DataProductExternalSourceLink.data_product_version_id
            == DataProductVersion.id,
        )
        .where(
            DataProduct.space_id == context.space_id,
            DataProductPublication.status == "active",
        )
    )
    clause = _search_clause(
        terms,
        DataProduct.product_code,
        DataProduct.name,
        DataProduct.description,
        DataProduct.domain,
        DataProductVersion.content_summary,
        DataProductVersion.linkage_metadata,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = (
        await context.session.execute(
            statement.order_by(
                DataProduct.updated_at.desc(),
                DataProductVersion.version_no.desc(),
            ).limit(MAX_RESULTS)
        )
    ).all()
    items: list[AssistantResource] = []
    for product, version, provider, external_link in rows:
        capability = await resolve_data_service_capability(
            context.session,
            version=version,
            external_link=external_link,
        )
        payload = capability.to_payload()
        items.append(
            AssistantResource(
                key=f"service:{version.id}",
                kind="service",
                label="数据服务能力",
                title=product.name,
                subtitle=" · ".join(
                    (
                        provider.display_name,
                        str(payload["service_mode_label"]),
                        str(payload["runtime_availability_label"]),
                        str(payload["requestability_label"]),
                    )
                ),
                status=str(payload["runtime_availability_label"]),
                path="/data-catalog",
            )
        )
    return AssistantQueryResult(
        label="数据服务",
        unit="项",
        source="medtrust.data_service_capabilities",
        total=total,
        items=items,
    )


async def _search_public_datasets(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = (
        select(ExternalDatasetRecord)
        .join(ExternalCatalogSource)
        .where(
            ExternalCatalogSource.space_id == context.space_id,
            ExternalCatalogSource.resource_kind == "dataset",
            ExternalDatasetRecord.status == "active",
        )
    )
    clause = _search_clause(
        terms,
        ExternalDatasetRecord.external_id,
        ExternalDatasetRecord.canonical_name,
        ExternalDatasetRecord.display_name_cn,
        ExternalDatasetRecord.display_name_en,
        ExternalDatasetRecord.disease_areas,
        ExternalDatasetRecord.organs,
        ExternalDatasetRecord.modalities,
        ExternalDatasetRecord.task_types,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = list(
        (
            await context.session.scalars(
                statement.order_by(ExternalDatasetRecord.canonical_name).limit(MAX_RESULTS)
            )
        ).all()
    )
    items = [
        AssistantResource(
            key=f"data:{row.id}",
            kind="data",
            label="公共候选数据集",
            title=row.display_name_cn or row.canonical_name,
            subtitle=" · ".join(
                value
                for value in (
                    row.source_catalog,
                    ", ".join(str(item) for item in row.disease_areas[:2]),
                    ", ".join(str(item) for item in row.modalities[:2]),
                    "目录元数据，需治理后申请",
                )
                if value
            ),
            status=row.license_status,
            path="/external-catalog/datasets",
        )
        for row in rows
    ]
    return AssistantQueryResult(
        label="公共候选数据集",
        unit="条",
        source="medtrust.external_dataset_catalog",
        total=total,
        items=items,
    )


async def search_model_products(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    if _PUBLIC_PATTERN.search(query_text):
        return await _search_public_models(context, query_text)

    session = context.session
    terms = _terms(query_text)
    statement = (
        select(
            ModelProduct,
            ModelVersion,
            Organization,
            ModelProductExternalSourceLink.id,
        )
        .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
        .join(Organization, Organization.id == ModelProduct.provider_organization_id)
        .outerjoin(
            ModelProductExternalSourceLink,
            ModelProductExternalSourceLink.model_version_id == ModelVersion.id,
        )
        .where(ModelProduct.space_id == context.space_id)
    )
    scope = _product_search_scope(
        role=context.role,
        provider_role="model_provider",
        query=query_text,
    )
    published = scope == "published"
    if published:
        statement = statement.join(
            ModelPublication,
            ModelPublication.model_version_id == ModelVersion.id,
        ).where(ModelPublication.status == "active")
    elif scope == "owned":
        statement = statement.where(
            ModelProduct.provider_organization_id == context.actor.organization_id
        )
    elif scope == "pending":
        statement = statement.where(ModelVersion.status == "under_review")
    clause = _search_clause(
        terms,
        ModelProduct.product_code,
        ModelProduct.name,
        ModelProduct.description,
        ModelProduct.domain,
        ModelVersion.compatibility_metadata,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(session, statement)
    rows = (
        await session.execute(
            statement.order_by(ModelProduct.updated_at.desc(), ModelVersion.version_no.desc())
            .limit(MAX_RESULTS)
        )
    ).all()
    label = (
        "已发布模型产品"
        if scope == "published"
        else "待审核模型产品"
        if scope == "pending"
        else "模型产品"
    )
    items = [
        AssistantResource(
            key=f"model:{version.id}",
            kind="model",
            label=label,
            title=product.name,
            subtitle=" · ".join(
                value
                for value in (
                    provider.display_name,
                    product.domain,
                    str(version.compatibility_metadata.get("task_type") or ""),
                    _service_mode_summary(
                        "model",
                        version.default_policy_template,
                        published=published,
                        external=source_link_id is not None,
                    ),
                )
                if value
            ),
            status=version.status,
            path=_product_path(
                context.role,
                "model",
                version.id,
                provider_organization_id=product.provider_organization_id,
                actor_organization_id=context.actor.organization_id,
            ),
        )
        for product, version, provider, source_link_id in rows
    ]
    return AssistantQueryResult(
        label=label,
        unit="项",
        source="medtrust.model_products",
        total=total,
        items=items,
    )


async def _search_public_models(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = (
        select(ExternalModelRecord)
        .join(ExternalCatalogSource)
        .where(
            ExternalCatalogSource.space_id == context.space_id,
            ExternalCatalogSource.resource_kind == "model",
            ExternalModelRecord.status == "active",
        )
    )
    clause = _search_clause(
        terms,
        ExternalModelRecord.external_model_id,
        ExternalModelRecord.canonical_name,
        ExternalModelRecord.display_name_cn,
        ExternalModelRecord.display_name_en,
        ExternalModelRecord.paper_title,
        ExternalModelRecord.model_categories,
        ExternalModelRecord.disease_areas,
        ExternalModelRecord.organs,
        ExternalModelRecord.modalities,
        ExternalModelRecord.task_types,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = list(
        (
            await context.session.scalars(
                statement.order_by(ExternalModelRecord.canonical_name).limit(MAX_RESULTS)
            )
        ).all()
    )
    items = [
        AssistantResource(
            key=f"model:{row.id}",
            kind="model",
            label="公共候选模型",
            title=row.display_name_cn or row.canonical_name,
            subtitle=" · ".join(
                value
                for value in (
                    row.upstream_provider or row.source_catalog,
                    ", ".join(str(item) for item in row.disease_areas[:2]),
                    ", ".join(str(item) for item in row.task_types[:2]),
                    "目录元数据，未注册执行",
                )
                if value
            ),
            status=row.weights_status,
            path="/external-catalog/models",
        )
        for row in rows
    ]
    return AssistantQueryResult(
        label="公共候选模型",
        unit="条",
        source="medtrust.external_model_catalog",
        total=total,
        items=items,
    )


async def get_product_details(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    wants_data = bool(re.search(r"数据|dataset|data", query_text, re.IGNORECASE))
    wants_model = bool(re.search(r"模型|算法|model", query_text, re.IGNORECASE))
    if not wants_data and not wants_model:
        wants_data = wants_model = True

    # "公开" can be part of an already published product's display name.  Detail
    # lookups must stay in the governed product catalog instead of being diverted
    # to the metadata-only external candidate catalog.
    catalog_query = re.sub(r"公共|公开|候选", " ", query_text)
    results: list[AssistantQueryResult] = []
    if wants_data:
        results.append(await search_data_products(context, catalog_query))
    if wants_model:
        results.append(await search_model_products(context, catalog_query))

    items: list[AssistantResource] = []
    for result in results:
        for item in result.items:
            label = "数据产品详情" if item.kind == "data" else "模型产品详情"
            items.append(item.model_copy(update={"label": label}))
            if len(items) == MAX_RESULTS:
                break
        if len(items) == MAX_RESULTS:
            break
    explicit_items = _explicit_product_references(items, query_text)
    if explicit_items:
        items = explicit_items
    return AssistantQueryResult(
        label="产品详情",
        unit="项",
        source="medtrust.product_catalog_details",
        total=len(items) if explicit_items else sum(result.total for result in results),
        items=items,
    )


def _application_statement(context: AssistantQueryContext) -> Any:
    statement = select(Application).where(Application.space_id == context.space_id)
    if context.role == "data_requester":
        statement = statement.where(
            Application.applicant_organization_id == context.actor.organization_id
        )
    elif context.role == "data_provider":
        statement = statement.where(
            Application.provider_organization_id == context.actor.organization_id,
            Application.status != "draft",
        )
    elif context.role == "model_provider":
        statement = statement.join(
            ApplicationModelSelection,
            ApplicationModelSelection.application_id == Application.id,
        ).where(
            ApplicationModelSelection.model_provider_organization_id
            == context.actor.organization_id,
            Application.status != "draft",
        )
    return statement


def _service_access_statement(context: AssistantQueryContext) -> Any:
    statement = select(ServiceAccessRequest).where(
        ServiceAccessRequest.space_id == context.space_id
    )
    if context.role == "data_requester":
        return statement.where(
            ServiceAccessRequest.requester_organization_id
            == context.actor.organization_id
        )
    if context.role == "data_provider":
        return statement.where(
            ServiceAccessRequest.provider_organization_id
            == context.actor.organization_id,
            ServiceAccessRequest.product_kind == "data",
        )
    if context.role == "model_provider":
        return statement.where(
            ServiceAccessRequest.provider_organization_id
            == context.actor.organization_id,
            ServiceAccessRequest.product_kind == "model",
        )
    return statement


async def get_request_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    authorization_only = bool(
        _AUTHORIZATION_REQUEST_PATTERN.search(query_text)
        and not _COMPUTE_REQUEST_PATTERN.search(query_text)
    )
    compute_only = bool(
        _COMPUTE_REQUEST_PATTERN.search(query_text)
        and not _AUTHORIZATION_REQUEST_PATTERN.search(query_text)
    )
    items: list[AssistantResource] = []
    total = 0
    if not authorization_only:
        statement = _application_statement(context)
        if _PENDING_PATTERN.search(query_text):
            statement = statement.where(
                Application.status.in_(("submitted", "prechecking", "provider_review"))
            )
        clause = _search_clause(
            terms,
            Application.application_number,
            Application.purpose,
            Application.algorithm_name,
            Application.status,
        )
        if clause is not None:
            statement = statement.where(clause)
        total += await _total(context.session, statement)
        rows = list(
            (
                await context.session.scalars(
                    statement.order_by(Application.updated_at.desc()).limit(MAX_RESULTS)
                )
            ).unique().all()
        )
        items.extend(
            AssistantResource(
                key=f"application:{row.id}",
                kind="application",
                label="受控计算申请",
                title=row.application_number,
                subtitle=" · ".join(
                    value for value in (row.algorithm_name, row.purpose) if value
                ),
                status=row.status,
                path=f"/applications/{row.id}",
            )
            for row in rows
        )
    if not compute_only:
        access_statement = _service_access_statement(context)
        if _PENDING_PATTERN.search(query_text):
            access_statement = access_statement.where(
                ServiceAccessRequest.status.in_(("submitted", "provider_approved"))
            )
        access_clause = _search_clause(
            terms,
            ServiceAccessRequest.request_number,
            ServiceAccessRequest.purpose,
            ServiceAccessRequest.intended_use,
            ServiceAccessRequest.status,
            ServiceAccessRequest.product_snapshot,
        )
        if access_clause is not None:
            access_statement = access_statement.where(access_clause)
        total += await _total(context.session, access_statement)
        access_rows = list(
            (
                await context.session.scalars(
                    access_statement.order_by(
                        ServiceAccessRequest.updated_at.desc()
                    ).limit(MAX_RESULTS)
                )
            ).all()
        )
        items.extend(
            AssistantResource(
                key=f"service-access:{row.id}",
                kind="application",
                label="授权申请",
                title=row.request_number,
                subtitle=" · ".join(
                    value
                    for value in (
                        str(row.product_snapshot.get("name") or ""),
                        SERVICE_MODE_LABELS.get(row.service_mode),
                        row.purpose,
                    )
                    if value
                ),
                status=row.status,
                path="/applications",
            )
            for row in access_rows
        )
    items = items[:MAX_RESULTS]
    label = "授权申请" if authorization_only else "受控计算申请" if compute_only else "服务申请"
    return AssistantQueryResult(
        label=label,
        unit="项",
        source="medtrust.service_requests",
        total=total,
        items=items,
    )


async def get_contract_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    latest_revision = (
        select(
            ContractRevision.contract_id.label("contract_id"),
            func.max(ContractRevision.revision_no).label("revision_no"),
        )
        .group_by(ContractRevision.contract_id)
        .subquery()
    )
    visible_party = (
        select(ContractParty.contract_revision_id)
        .where(
            ContractParty.contract_revision_id == ContractRevision.id,
            ContractParty.organization_id == context.actor.organization_id,
        )
        .exists()
    )
    statement = (
        select(Contract, ContractRevision)
        .join(latest_revision, latest_revision.c.contract_id == Contract.id)
        .join(
            ContractRevision,
            and_(
                ContractRevision.contract_id == latest_revision.c.contract_id,
                ContractRevision.revision_no == latest_revision.c.revision_no,
            ),
        )
        .where(
            Contract.space_id == context.space_id,
            visible_party,
        )
    )
    clause = _search_clause(
        terms,
        Contract.contract_number,
        ContractRevision.name,
        ContractRevision.summary,
        ContractRevision.status,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = (
        await context.session.execute(
            statement.order_by(Contract.created_at.desc(), ContractRevision.revision_no.desc())
            .limit(MAX_RESULTS)
        )
    ).all()
    items = [
        AssistantResource(
            key=f"contract:{contract.id}:{revision.id}",
            kind="contract",
            label="数字合约",
            title=revision.name or contract.contract_number,
            subtitle=" · ".join(
                value for value in (contract.contract_number, revision.summary) if value
            ),
            status=revision.status,
            path=f"/contracts/{contract.id}",
        )
        for contract, revision in rows
    ]
    return AssistantQueryResult(
        label="数字合约",
        unit="份",
        source="medtrust.contracts",
        total=total,
        items=items,
    )


async def get_execution_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = (
        select(ComputeJob, Contract)
        .join(Contract, Contract.id == ComputeJob.contract_id)
        .join(
            ContractParty,
            ContractParty.contract_revision_id == ComputeJob.contract_revision_id,
        )
        .where(
            ComputeJob.space_id == context.space_id,
            ContractParty.organization_id == context.actor.organization_id,
        )
    )
    clause = _search_clause(
        terms,
        Contract.contract_number,
        ComputeJob.purpose_code,
        ComputeJob.status,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = (
        await context.session.execute(
            statement.order_by(ComputeJob.created_at.desc()).limit(MAX_RESULTS)
        )
    ).all()
    items = [
        AssistantResource(
            key=f"execution:{job.id}",
            kind="execution",
            label="执行任务",
            title=f"执行任务 · {contract.contract_number}",
            subtitle=job.purpose_code,
            status=job.status,
            path=f"/execution/{contract.id}",
        )
        for job, contract in rows
    ]
    return AssistantQueryResult(
        label="执行任务",
        unit="项",
        source="medtrust.compute_jobs",
        total=total,
        items=items,
    )


async def get_result_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = (
        select(Artifact, Contract)
        .join(ComputeJob, ComputeJob.id == Artifact.compute_job_id)
        .join(Contract, Contract.id == ComputeJob.contract_id)
        .join(
            ContractParty,
            ContractParty.contract_revision_id == ComputeJob.contract_revision_id,
        )
        .where(
            Artifact.space_id == context.space_id,
            ContractParty.organization_id == context.actor.organization_id,
        )
    )
    clause = _search_clause(
        terms,
        Contract.contract_number,
        Artifact.artifact_type,
        Artifact.release_status,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = (
        await context.session.execute(
            statement.order_by(Artifact.created_at.desc()).limit(MAX_RESULTS)
        )
    ).all()
    items = [
        AssistantResource(
            key=f"result:{artifact.id}",
            kind="result",
            label="结果记录",
            title=f"{artifact.artifact_type} · {contract.contract_number}",
            subtitle=f"{artifact.size_bytes} bytes",
            status=artifact.release_status,
            path=f"/results/{artifact.id}",
        )
        for artifact, contract in rows
    ]
    return AssistantQueryResult(
        label="结果记录",
        unit="项",
        source="medtrust.artifacts",
        total=total,
        items=items,
    )


async def get_lifecycle_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = select(ProductLifecycleRequest).where(
        ProductLifecycleRequest.space_id == context.space_id
    )
    if context.role != "space_operator":
        statement = statement.where(
            ProductLifecycleRequest.requested_by_organization_id
            == context.actor.organization_id
        )
    if _PENDING_PATTERN.search(query_text):
        statement = statement.where(ProductLifecycleRequest.status == "pending")
    clause = _search_clause(
        terms,
        ProductLifecycleRequest.reason,
        ProductLifecycleRequest.action,
        ProductLifecycleRequest.target_type,
        ProductLifecycleRequest.status,
    )
    if clause is not None:
        statement = statement.where(clause)
    total = await _total(context.session, statement)
    rows = list(
        (
            await context.session.scalars(
                statement.order_by(ProductLifecycleRequest.requested_at.desc()).limit(MAX_RESULTS)
            )
        ).all()
    )
    items = [
        AssistantResource(
            key=f"lifecycle:{row.id}",
            kind="lifecycle",
            label="生命周期事项",
            title=f"{row.target_type} · {row.action}",
            subtitle=row.reason,
            status=row.status,
            path="/lifecycle",
        )
        for row in rows
    ]
    return AssistantQueryResult(
        label="生命周期事项",
        unit="项",
        source="medtrust.product_lifecycle_requests",
        total=total,
        items=items,
    )


_COMPATIBILITY_STATUS_LABELS = {
    "not_assessed": "尚未评估",
    "external_declaration_only": "仅有外部声明",
    "static_schema_compatible": "静态结构兼容",
    "static_schema_compatible_with_transformation": "经转换后静态兼容",
    "static_schema_incompatible": "静态结构不兼容",
    "insufficient_metadata": "元数据不足",
    "executed": "已有运行证据",
    "execution_failed": "运行失败",
    "verified": "平台已验证",
    "superseded": "已被替代",
    "archived": "已归档",
}
_PAIR_EVIDENCE_PATTERN = re.compile(
    r"(?:数据|数据集|数据产品).{0,48}(?:模型|算法).{0,32}(?:适配|兼容|证据)|"
    r"(?:模型|算法).{0,48}(?:数据|数据集|数据产品).{0,32}(?:适配|兼容|证据)",
    re.IGNORECASE,
)


def _reference_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _reference_tokens(*values: str) -> set[str]:
    ignored = {"data", "dataset", "model", "product", "public", "version"}
    tokens = {
        token.casefold()
        for value in values
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", value)
    }
    return tokens - ignored


def _full_reference_match(query: str, *, name: str, code: str) -> bool:
    query_value = _reference_text(query)
    base_name = re.split(r"[（(]", name, maxsplit=1)[0]
    candidates = (_reference_text(name), _reference_text(base_name), _reference_text(code))
    return any(len(candidate) >= 4 and candidate in query_value for candidate in candidates)


def _pair_is_referenced(
    query: str,
    *,
    data_name: str,
    data_code: str,
    model_name: str,
    model_code: str,
) -> bool:
    data_full = _full_reference_match(query, name=data_name, code=data_code)
    model_full = _full_reference_match(query, name=model_name, code=model_code)
    data_tokens = _reference_tokens(data_name, data_code)
    model_tokens = _reference_tokens(model_name, model_code)
    query_value = query.casefold()
    shared_matches = {
        token for token in data_tokens & model_tokens if token in query_value
    }
    data_unique_match = any(
        token in query_value for token in data_tokens - model_tokens
    )
    model_unique_match = any(
        token in query_value for token in model_tokens - data_tokens
    )
    data_match = data_full or data_unique_match or bool(
        shared_matches and (model_full or model_unique_match)
    )
    model_match = model_full or model_unique_match or bool(
        shared_matches and (data_full or data_unique_match)
    )
    return data_match and model_match


def _transformation_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("code") or item.get("description")
        if name:
            names.append(str(name)[:160])
    return names[:20]


async def check_data_model_compatibility(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    statement = (
        select(
            DatasetModelRelation,
            DatasetModelEvidence,
            DataProduct,
            DataProductVersion,
            ModelProduct,
            ModelVersion,
        )
        .join(DataProduct, DataProduct.id == DatasetModelRelation.data_product_id)
        .join(
            DataProductVersion,
            DataProductVersion.id == DatasetModelRelation.data_product_version_id,
        )
        .join(ModelProduct, ModelProduct.id == DatasetModelRelation.model_product_id)
        .join(
            ModelVersion,
            ModelVersion.id == DatasetModelRelation.model_product_version_id,
        )
        .outerjoin(
            DatasetModelEvidence,
            DatasetModelEvidence.id == DatasetModelRelation.current_evidence_id,
        )
        .where(
            DatasetModelRelation.space_id == context.space_id,
            DatasetModelRelation.active.is_(True),
        )
    )
    if context.role != "space_operator":
        statement = (
            statement.join(
                DataProductPublication,
                DataProductPublication.data_product_version_id
                == DatasetModelRelation.data_product_version_id,
            )
            .join(
                ModelPublication,
                ModelPublication.model_version_id
                == DatasetModelRelation.model_product_version_id,
            )
            .where(
                DatasetModelRelation.public_visible.is_(True),
                DataProduct.lifecycle_status == "active",
                DataProductVersion.status == "approved",
                DataProductPublication.status == "active",
                ModelProduct.lifecycle_status == "active",
                ModelVersion.status == "approved",
                ModelPublication.status == "active",
            )
        )
    terms = _terms(query_text)
    clause = _search_clause(
        terms,
        DataProduct.product_code,
        DataProduct.name,
        DataProductVersion.version_label,
        ModelProduct.product_code,
        ModelProduct.name,
        ModelVersion.version_label,
    )
    if clause is not None:
        statement = statement.where(clause)
    pair_requested = bool(_PAIR_EVIDENCE_PATTERN.search(query_text)) and "证据矩阵" not in query_text
    total = await _total(context.session, statement) if not pair_requested else 0
    rows = (
        await context.session.execute(
            statement.order_by(DatasetModelRelation.updated_at.desc()).limit(
                100 if pair_requested else MAX_RESULTS
            )
        )
    ).all()
    if pair_requested:
        rows = [
            row
            for row in rows
            if _pair_is_referenced(
                query_text,
                data_name=row[2].name,
                data_code=row[2].product_code,
                model_name=row[4].name,
                model_code=row[4].product_code,
            )
        ]
        total = len(rows)
        rows = rows[:MAX_RESULTS]
    evidence_items: list[AssistantCompatibilityEvidence] = []
    resources: list[AssistantResource] = []
    for relation, evidence, data_product, data_version, model_product, model_version in rows:
        status_label = _COMPATIBILITY_STATUS_LABELS.get(
            relation.current_status,
            relation.current_status,
        )
        path = _product_path(
            context.role,
            "data",
            data_version.id,
            provider_organization_id=data_product.provider_organization_id,
            actor_organization_id=context.actor.organization_id,
        )
        evidence_items.append(
            AssistantCompatibilityEvidence(
                relation_id=str(relation.id),
                data_name=data_product.name,
                data_version=data_version.version_label,
                model_name=model_product.name,
                model_version=model_version.version_label,
                status=relation.current_status,
                status_label=status_label,
                evidence_level=relation.strongest_evidence_level,
                evidence_type=evidence.evidence_type if evidence else None,
                outcome=evidence.outcome if evidence else None,
                evidence_note=evidence.evidence_note if evidence else None,
                blocking_reasons=list(evidence.blocking_reasons or []) if evidence else [],
                warning_reasons=list(evidence.warning_reasons or []) if evidence else [],
                transformation_requirements=_transformation_names(
                    evidence.transformation_requirements if evidence else []
                ),
                assessed_at=evidence.created_at.isoformat() if evidence else None,
                path=path,
            )
        )
        resources.append(
            AssistantResource(
                key=f"compatibility-evidence:{relation.id}",
                kind="data",
                label="数据—模型适配证据",
                title=f"{data_product.name} × {model_product.name}",
                subtitle=(
                    f"{data_version.version_label} × {model_version.version_label} · "
                    f"{relation.strongest_evidence_level}"
                ),
                status=status_label,
                path=path,
            )
        )
    if not evidence_items:
        evidence_items.append(
            AssistantCompatibilityEvidence(
                status="not_assessed",
                status_label="尚未评估",
                evidence_note=(
                    "当前账号可见范围内没有与所述产品版本配对的已保存证据；"
                    "请核对数据和模型名称或版本。本次查询未触发检查。"
                    if pair_requested
                    else "当前账号可见范围内没有已保存的版本配对证据；本次查询未触发检查。"
                ),
            )
        )
    return AssistantQueryResult(
        label="数据—模型适配证据",
        unit="组",
        source="medtrust.dataset_model_evidence",
        total=total,
        items=resources,
        compatibility_evidence=evidence_items,
    )


def _lineage_state(*, status: str, complete: bool) -> str:
    normalized = status.casefold()
    if any(
        token in normalized
        for token in ("failed", "rejected", "revoked", "blocked", "denied", "error", "cancelled")
    ):
        return "blocked"
    if complete:
        return "completed"
    if normalized in {"running", "active", "checking", "claimed", "executing"}:
        return "active"
    return "pending"


async def get_execution_lineage(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    # Reuse the roadshow's existing role-scoped chain projection. Only the safe
    # node list is copied; digests, storage coordinates and internal snapshots are not.
    from app.api.routes.roadshow_experience import _chain_rows, _project_chain

    applications = await _chain_rows(
        context.session,
        SimpleNamespace(space_id=context.space_id),
        context.actor,
    )
    terms = _terms(query_text)
    if terms:
        applications = [
            application
            for application in applications
            if any(
                term in " ".join(
                    str(value or "").casefold()
                    for value in (
                        application.application_number,
                        application.purpose,
                        application.algorithm_name,
                    )
                )
                for term in terms
            )
        ]
    total = len(applications)
    lineages: list[AssistantExecutionLineage] = []
    for application in applications[:5]:
        payload, _ = await _project_chain(context.session, application)
        nodes = [
            AssistantLineageNode(
                key=str(node["key"]),
                label=str(node["label"]),
                number=str(node["number"]) if node.get("number") is not None else None,
                status=str(node["status"]),
                complete=bool(node["complete"]),
                state=_lineage_state(
                    status=str(node["status"]),
                    complete=bool(node["complete"]),
                ),
                responsible_role=(
                    str(node["responsible_role"])
                    if node.get("responsible_role") is not None
                    else None
                ),
            )
            for node in payload["nodes"]
        ]
        lineages.append(
            AssistantExecutionLineage(
                application_id=str(payload["application_id"]),
                application_number=str(payload["application_number"]),
                scenario_name=str(payload.get("scenario_name") or "未命名研究"),
                status=str(payload["status"]),
                completed_nodes=int(payload["completed_nodes"]),
                total_nodes=int(payload["total_nodes"]),
                next_role=(str(payload["next_role"]) if payload.get("next_role") else None),
                next_action=(
                    str(payload["next_action"]) if payload.get("next_action") else None
                ),
                path=f"/applications/{application.id}",
                nodes=nodes,
            )
        )
    return AssistantQueryResult(
        label="执行血缘",
        unit="条",
        source="medtrust.execution_lineage_projection",
        total=total,
        items=[],
        lineage=lineages,
    )


async def read_compatibility_status(
    context: AssistantQueryContext,
    query_text: str,
) -> AssistantQueryResult:
    terms = _terms(query_text)
    statement = _application_statement(context).join(
        ApplicationRequestedAction,
        ApplicationRequestedAction.application_id == Application.id,
    )
    clause = _search_clause(
        terms,
        Application.application_number,
        Application.algorithm_name,
        Application.purpose,
    )
    if clause is not None:
        statement = statement.where(clause)
    id_statement = statement.with_only_columns(
        Application.id,
        Application.updated_at,
    ).distinct()
    total = await _total(context.session, id_statement)
    application_ids = list(
        (
            await context.session.scalars(
                id_statement.order_by(Application.updated_at.desc()).limit(MAX_RESULTS)
            )
        ).all()
    )
    applications = list(
        (
            await context.session.scalars(
                select(Application).where(Application.id.in_(application_ids))
            )
        ).all()
    )
    application_by_id = {application.id: application for application in applications}
    rows = [
        application_by_id[application_id]
        for application_id in application_ids
        if application_id in application_by_id
    ]
    actions = list(
        (
            await context.session.scalars(
                select(ApplicationRequestedAction).where(
                    ApplicationRequestedAction.application_id.in_(application_ids)
                )
            )
        ).all()
    )
    compatibility_by_application: dict[Any, dict[str, Any]] = {}
    for action in actions:
        compatibility = action.parameters.get("compatibility")
        if isinstance(compatibility, dict):
            compatibility_by_application.setdefault(action.application_id, compatibility)
    items: list[AssistantResource] = []
    for application in rows:
        compatibility = compatibility_by_application.get(application.id)
        overall = compatibility.get("overall") if isinstance(compatibility, dict) else None
        items.append(
            AssistantResource(
                key=f"compatibility:{application.id}",
                kind="application",
                label="兼容性状态",
                title=f"{application.application_number} · 兼容性",
                subtitle=application.algorithm_name,
                status=str(overall or "待检查"),
                path=f"/applications/{application.id}",
            )
        )
    return AssistantQueryResult(
        label="兼容性状态",
        unit="项",
        source="medtrust.saved_compatibility_reports",
        total=total,
        items=items,
    )
