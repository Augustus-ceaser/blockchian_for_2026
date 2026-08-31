from fastapi import APIRouter

from app.api.routes.system import router as system_router
from app.api.routes.auth import router as auth_router
from app.api.routes.business import router as business_router
from app.api.routes.data_products import router as data_products_router
from app.api.routes.model_products import router as model_products_router
from app.api.routes.applications import router as applications_router
from app.api.routes.contracts import router as contracts_router
from app.api.routes.execution_readiness import router as execution_readiness_router
from app.api.routes.roadshow import router as roadshow_router
from app.api.routes.roadshow_experience import router as roadshow_experience_router
from app.api.routes.result_release import router as result_release_router
from app.api.routes.product_lifecycle import router as product_lifecycle_router
from app.api.routes.policy_control import router as policy_control_router
from app.api.routes.external_catalog import router as external_catalog_router
from app.api.routes.external_model_catalog import router as external_model_catalog_router
from app.api.routes.dataset_model_evidence import router as dataset_model_evidence_router
from app.api.routes.asset_materialization import router as asset_materialization_router
from app.api.routes.roadshow_seal import router as roadshow_seal_router
from app.api.routes.connector_control import router as connector_control_router
from app.api.routes.role_assistant import router as role_assistant_router
from app.api.routes.service_access import router as service_access_router
from app.api.routes.commerce import router as commerce_router

api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(auth_router)
api_router.include_router(business_router)
api_router.include_router(data_products_router)
api_router.include_router(model_products_router)
api_router.include_router(applications_router)
api_router.include_router(contracts_router)
api_router.include_router(execution_readiness_router)
api_router.include_router(result_release_router)
api_router.include_router(product_lifecycle_router)
api_router.include_router(external_catalog_router)
api_router.include_router(external_model_catalog_router)
api_router.include_router(dataset_model_evidence_router)
api_router.include_router(asset_materialization_router)
api_router.include_router(roadshow_seal_router)
api_router.include_router(connector_control_router)
api_router.include_router(policy_control_router)
api_router.include_router(roadshow_experience_router)
api_router.include_router(roadshow_router)
api_router.include_router(role_assistant_router)
api_router.include_router(service_access_router)
api_router.include_router(commerce_router)
