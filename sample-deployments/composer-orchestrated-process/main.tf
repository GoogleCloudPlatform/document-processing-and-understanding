# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
provider "google" {
  project               = var.project_id
  user_project_override = true
  billing_project       = var.project_id
  default_labels        = local.dpu_label
}

locals {
  cloud_run_job_name = "doc-processor"
  dpu_label = {
    goog-packaged-solution : "dpu-solution"
  }
}

module "common_infra" {
  source     = "../../components/common-infra/terraform"
  project_id = var.project_id
  region     = var.region
}

module "project_services" {
  source                      = "terraform-google-modules/project-factory/google//modules/project_services"
  version                     = "14.5.0"
  project_id                  = var.project_id
  disable_services_on_destroy = false
  disable_dependent_services  = false
  activate_apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com"
  ]
}

resource "google_discovery_engine_data_store" "dpu_ds" {
  project                     = module.project_services.project_id
  location                    = var.vertex_ai_data_store_region
  data_store_id               = "dpu-doc-store"
  display_name                = "Document Processing & Understanding"
  industry_vertical           = "GENERIC"
  content_config              = "CONTENT_REQUIRED"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false
}

resource "google_discovery_engine_search_engine" "basic" {
  project = module.project_services.project_id
  # TODO: Change this
  engine_id      = module.project_services.project_id
  collection_id  = "default_collection"
  location       = var.vertex_ai_data_store_region
  display_name   = "Example Display Name"
  data_store_ids = [google_discovery_engine_data_store.dpu_ds.data_store_id]
  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE"
    search_add_ons = ["SEARCH_ADD_ON_LLM"]
  }
}

module "processor" {
  source             = "../../components/processing/deployments/cloud_run"
  project_id         = var.project_id
  region             = var.region
  bq_region          = var.region
  gcs_region         = var.region
  repository_region  = var.region
  artifact_repo_name = module.common_infra.artifact_repo.name
  cloud_run_job_name = local.cloud_run_job_name
}

module "form_parser_processor" {
  source            = "../../components/processing/form_parser/deployment"
  project_id        = var.project_id
  region            = var.region
  location          = var.vertex_ai_data_store_region
  gcs_input_prefix  = module.common_infra.gcs_process_bucket_name
  gcs_output_prefix = module.common_infra.gcs_process_bucket_name
}

module "dpu_workflow" {
  source           = "../../components/dpu-workflow/terraform"
  region           = var.region
  project_id       = var.project_id
  vpc_network_name = module.common_infra.vpc_network_name
  vpc_network_id   = module.common_infra.vpc_network_id
  composer_env_variables = {
    DPU_OUTPUT_DATASET     = module.common_infra.bq_store_dataset_id
    DPU_INPUT_BUCKET       = module.common_infra.gcs_input_bucket_name
    DPU_PROCESS_BUCKET     = module.common_infra.gcs_process_bucket_name
    DPU_REJECT_BUCKET      = module.common_infra.gcs_reject_bucket_name
    DPU_REGION             = var.region
    DPU_DATA_STORE_REGION  = var.vertex_ai_data_store_region
    DOC_PROCESSOR_JOB_NAME = module.processor.cloud_run_job_name
    DPU_DATA_STORE_ID      = google_discovery_engine_data_store.dpu_ds.data_store_id
    FORMS_PARSER_JOB_NAME  = module.form_parser_processor.cloud_run_job_name
  }
}

module "dpu_ui" {
  source                      = "../../components/webui/terraform"
  count                       = var.deploy_ui ? 1 : 0
  project_id                  = var.project_id
  region                      = var.region
  application_title           = "DPU Web UI"
  iap_admin_account           = var.iap_admin_account
  artifact_repo               = module.common_infra.artifact_repo
  iap_access_domains          = var.iap_access_domains
  vertex_ai_data_store_region = var.vertex_ai_data_store_region
  agent_builder_data_store_id = google_discovery_engine_data_store.dpu_ds.data_store_id
  agent_builder_search_id     = google_discovery_engine_search_engine.basic.engine_id
  vpc_network_name            = module.common_infra.vpc_network_name
  vpc_network_id              = module.common_infra.vpc_network_id
  gcs_object_store            = module.common_infra.gcs_process_bucket_name
  app_engine_service_name     = var.webui_service_name
}

# Depends on: input bucket, artefactory (registury_url), and docprocessor service account
resource "local_file" "env_file" {
  filename = "${path.module}/../../.env"
  content = templatefile("${path.module}/env.template", {

    # Common infrastructure
    project_id         = var.project_id,
    region             = var.region,
    bq_region          = var.region,
    gcs_region         = var.region,
    repository_region  = var.region,
    artifact_repo_name = module.common_infra.artifact_repo.name
    gcs_input_bucket   = module.common_infra.gcs_input_bucket_name
    gcs_process_bucket = module.common_infra.gcs_process_bucket_name
    gcs_reject_bucket  = module.common_infra.gcs_reject_bucket_name

    # Cloud run specific..
    cloud_run_job_name = local.cloud_run_job_name
    service_account    = module.processor.doc_processor_service_account

    # Agent builder
    agent_builder_location      = var.vertex_ai_data_store_region
    agent_builder_data_store_id = google_discovery_engine_data_store.dpu_ds.data_store_id
    agent_builder_search_id     = google_discovery_engine_search_engine.basic.engine_id
  })
}
