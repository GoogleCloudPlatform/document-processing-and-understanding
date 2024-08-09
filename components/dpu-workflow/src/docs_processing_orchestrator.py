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

# pylint: disable=import-error

from datetime import datetime, timedelta
from collections import defaultdict
import os
import random
import string
import sys

import pdf_classifier
from airflow import DAG  # type: ignore
from airflow.models.param import Param  # type: ignore
from airflow.operators.python import (BranchPythonOperator,  # type: ignore
                                      PythonOperator)
from airflow.providers.google.cloud.operators.bigquery import BigQueryCreateEmptyTableOperator  # type: ignore
from airflow.providers.google.cloud.operators.cloud_run import CloudRunExecuteJobOperator  # type: ignore
from airflow.providers.google.cloud.operators.gcs import GCSListObjectsOperator  # type: ignore
from airflow.providers.google.cloud.transfers.gcs_to_gcs import GCSToGCSOperator  # type: ignore
from google.api_core.client_options import ClientOptions  # type: ignore
from google.api_core.gapic_v1.client_info import ClientInfo  # type: ignore
from google.cloud import discoveryengine, storage

sys.path.insert(0,os.path.abspath(os.path.dirname(__file__)))

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2024, 5, 12),  # Adjust as needed
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
}

USER_AGENT = "cloud-solutions/dpu-agent-builder-v1"

def get_supported_file_types(**context):
    file_list = context["ti"].xcom_pull(task_ids="list_all_input_files")
    file_type_to_processor = context["params"]["supported_files"]
    supported_file_types = set(
        item["file-suffix"].lower() for item in file_type_to_processor
    )

    files_by_type = defaultdict(list)
    for input_file in file_list:
        file_type = input_file.split(".")[-1].lower()
        if file_type in supported_file_types:
            files_by_type[file_type].append(input_file)

    context["ti"].xcom_push(key="types_to_process", value=files_by_type)


def has_files_to_process(**context):
    files_to_process = context["ti"].xcom_pull(key="types_to_process")
    if files_to_process:
        return "create_process_folder"
    else:
        return "skip_bucket_creation"


def generate_process_folder(**context):
    process_folder = f"docs-processing-{datetime.now().strftime('%d-%m-%Y')}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"
    context["ti"].xcom_push(key="process_folder", value=process_folder)


def generate_mv_params(**context):
    files_to_process = context["ti"].xcom_pull(key="types_to_process")
    process_folder = context["ti"].xcom_pull(key="process_folder")
    input_folder = context["params"]["input_folder"]
    input_folder_with_prefix = f"{input_folder}/" if input_folder else ""
    process_bucket = os.environ.get("DPU_PROCESS_BUCKET")
    parameter_obj_list = []
    
    for typ in files_to_process.keys():
        parameter_obj = {
            "source_object": f"{input_folder_with_prefix}*.{typ}",
            "destination_bucket": process_bucket,
            "destination_object": f"{process_folder}/{typ}/",
        }
        parameter_obj_list.append(parameter_obj)
    return parameter_obj_list


def data_store_import_docs(**context):
    data_store_region = os.environ.get("DPU_DATA_STORE_REGION")
    bq_table = context["ti"].xcom_pull(key="bigquery_table")

    client_options = (
        ClientOptions(
            api_endpoint=f"{data_store_region}-discoveryengine.googleapis.com"
        )
        if data_store_region != "global"
        else None
    )

    client = discoveryengine.DocumentServiceClient(
        client_options=client_options, client_info=ClientInfo(user_agent=USER_AGENT)
    )
    parent = client.branch_path(
        project=bq_table["project_id"],
        location=data_store_region,   # pyright: ignore[reportArgumentType]
        data_store=os.environ.get("DPU_DATA_STORE_ID"),   # pyright: ignore[reportArgumentType]
        branch="default_branch",
    )
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        bigquery_source=discoveryengine.BigQuerySource(
            project_id=bq_table["project_id"],
            dataset_id=bq_table["dataset_id"],
            table_id=bq_table["table_id"],
            data_schema="document",
        ),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    )
    operation = client.import_documents(request=request)
    response = operation.result()
    metadata = discoveryengine.ImportDocumentsMetadata(operation.metadata)
    print(response)
    print(metadata)
    return operation.operation.name


def generate_process_job_params(**context):
    mv_params = context["ti"].xcom_pull(
        key="return_value", task_ids="generate_files_move_parameters"
    )
    bq_table = context["ti"].xcom_pull(key="bigquery_table")
    doc_processor_job_name = os.environ.get("DOC_PROCESSOR_JOB_NAME")
    gcs_reject_bucket = os.environ.get("DPU_REJECT_BUCKET")

    process_job_params = []
    for mv_obj in mv_params:
        job_param = {
            "overrides": {
                "container_overrides": [
                    {
                        "name": f"{doc_processor_job_name}",
                        "args": [
                            f"gs://{mv_obj['destination_bucket']}/{mv_obj['destination_object']}",
                            f"gs://{gcs_reject_bucket}/{mv_obj['destination_object']}",
                            "--write_json=False",
                            f"--write_bigquery={bq_table['project_id']}.{bq_table['dataset_id']}.{bq_table['table_id']}",
                        ],
                        "clear_args": False,
                    }
                ],
                "task_count": 1,
                "timeout": "300s",
            }
        }
        process_job_params.append(job_param)
    return process_job_params


def generete_output_table_name(**context):
    process_folder = context["ti"].xcom_pull(key="process_folder")
    output_table_name = process_folder.replace("-", "_")
    context["ti"].xcom_push(key="output_table_name", value=output_table_name)

def generate_pdf_forms_folder(**context):
    process_folder = context["ti"].xcom_pull(key="process_folder")
    pdf_forms_folder = f"{process_folder}/pdf-forms"
    context["ti"].xcom_push(key="pdf_forms_folder", value=pdf_forms_folder)

def generate_pdf_forms_list(**context):
    process_folder = context["ti"].xcom_pull(key="process_folder")
    # pdf_forms_folder = context["ti"].xcom_pull(key="pdf_forms_folder")
    process_bucket = os.environ.get("DPU_PROCESS_BUCKET")

    project_id = context["params"]["pdf_classifier_project_id"]
    location = context["params"]["pdf_classifier_location"]
    processor_id = context["params"]["pdf_classifier_processor_id"]

    pdf_forms_list=[]
    storage_client = storage.Client()
    bucket = storage_client.bucket(process_bucket)

    if (processor_id is None or
            project_id.strip() == "" or
            location is None or
            location.strip() == "" or
            processor_id is None or
            processor_id.strip() == ""):
        return pdf_forms_list

    blobs = bucket.list_blobs(prefix=process_folder+"/pdf/")
    for blob in blobs:
        if process_bucket is not None:
            if pdf_classifier.is_form(project_id=project_id,
                                      location=location,
                                      processor_id=processor_id,
                                      file_storage_bucket=process_bucket,
                                      file_path=blob.name,
                                      mime_type="application/pdf"):
                pdf_form = {
                    "source_object": blob.name,
                    "destination_bucket": process_bucket,
                    "destination_object": f"{process_folder}/pdf-forms/input/"
                }
                pdf_forms_list.append(pdf_form)

    return pdf_forms_list


with DAG(
    "run_docs_processing",
    default_args=default_args,
    schedule_interval=None,
    params={
        "input_bucket": os.environ.get("DPU_INPUT_BUCKET"),
        "process_bucket": os.environ.get("DPU_PROCESS_BUCKET"),
        "input_folder": "",
        "supported_files": Param(
            [
                {"file-suffix": "pdf", "processor": "agent-builder"},
                {"file-suffix": "txt", "processor": "agent-builder"},
                {"file-suffix": "html", "processor": "agent-builder"},
                {"file-suffix": "msg", "processor": "dpu-doc-processor"},
                {"file-suffix": "zip", "processor": "dpu-doc-processor"},
                {"file-suffix": "xlsx", "processor": "dpu-doc-processor"},
                {"file-suffix": "xlsm", "processor": "dpu-doc-processor"},
            ],
            type="array",
            items={
                "type": "object",
                "properties": {
                    "file-suffix": {"type": "string"},
                    "processor": {"type": "string"},
                },
                "required": ["file-suffix", "processor"],
            },
        ),
        "pdf_classifier_project_id": "",
        "pdf_classifier_location": "",
        "pdf_classifier_processor_id": "",
    },
) as dag:
    GCS_Files = GCSListObjectsOperator(
        task_id="list_all_input_files",
        prefix="{{ params.input_folder if params.input_folder }}",
        bucket="{{ params.input_bucket }}",
    )

    process_supported_types = PythonOperator(
        task_id="process_supported_types",
        python_callable=get_supported_file_types,
        params={
            "supported_files": Param(
                "{{ params.supported_files }}",
                type="array",
                items={
                    "type": "object",
                    "properties": {
                        "file-suffix": {"type": "string"},
                        "processor": {"type": "string"},
                    },
                    "required": ["file-suffix", "processor"],
                },
            )
        },
        provide_context=True,
    )

    has_files = BranchPythonOperator(
        task_id="has_files_to_process",
        python_callable=has_files_to_process,
        provide_context=True,
    )

    create_process_folder = PythonOperator(
        task_id="create_process_folder",
        python_callable=generate_process_folder,
        provide_context=True,
    )

    skip_bucket_creation = PythonOperator(
        task_id="skip_bucket_creation",
        python_callable=lambda: print(
            "No supported file type found, processing ends here!"
        ),
    )

    generate_files_move_parameters = PythonOperator(
        task_id="generate_files_move_parameters",
        python_callable=generate_mv_params,
        provide_context=True,
    )

    move_to_processing = GCSToGCSOperator.partial(
        task_id="move_file",
        source_bucket="{{ params.input_bucket }}",
        move_object=True,
    ).expand_kwargs(generate_files_move_parameters.output)

    generate_pdf_forms_l = PythonOperator(
        task_id="generate_pdf_forms_list",
        python_callable=generate_pdf_forms_list,
        provide_context=True,
    )

    move_forms = GCSToGCSOperator.partial(
        task_id="move_forms",
        source_bucket="{{ params.process_bucket }}",
        move_object=True,
    ).expand_kwargs(generate_pdf_forms_l.output)

    execute_forms_parser = CloudRunExecuteJobOperator(
        project_id=os.environ.get("GCP_PROJECT"),
        region=os.environ.get("DPU_REGION"),
        task_id="execute_forms_parser",
        job_name=os.environ.get("FORMS_PARSER_JOB_NAME"),
        deferrable=False,
        overrides={
            "task_count": 1,
            "timeout": "300s",
        }
    )

    create_output_table_name = PythonOperator(
        task_id="create_output_table_name",
        python_callable=generete_output_table_name,
        provide_context=True,
    )

    create_output_table = create_bigquery_table = BigQueryCreateEmptyTableOperator(
        task_id="create_output_table",
        dataset_id=os.environ.get("DPU_OUTPUT_DATASET"),  # pyright: ignore[reportArgumentType]
        table_id="{{ ti.xcom_pull(task_ids='create_output_table_name', key='output_table_name') }}",
        schema_fields=[
            {"name": "id", "mode": "REQUIRED", "type": "STRING", "fields": []},
            {"name": "jsonData", "mode": "NULLABLE", "type": "STRING", "fields": []},
            {
                "name": "content",
                "type": "RECORD",
                "mode": "NULLABLE",
                "fields": [
                    {"name": "mimeType", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "uri", "type": "STRING", "mode": "NULLABLE"},
                ],
            },
        ],
    )

    create_process_job_params = PythonOperator(
        task_id="create_process_job_params",
        python_callable=generate_process_job_params,
        provide_context=True,
    )

    execute_doc_processors = CloudRunExecuteJobOperator.partial(
        project_id=os.environ.get("GCP_PROJECT"),
        region=os.environ.get("DPU_REGION"),
        task_id="execute_doc_processors",
        job_name=os.environ.get("DOC_PROCESSOR_JOB_NAME"),
        deferrable=False,
    ).expand_kwargs(create_process_job_params.output)

    import_docs_to_data_store = PythonOperator(
        task_id="import_docs_to_data_store",
        python_callable=data_store_import_docs,
        execution_timeout=timedelta(seconds=3600),
        provide_context=True,
    )

    (
        GCS_Files
        >> process_supported_types
        >> has_files
        >> [create_process_folder, skip_bucket_creation]
    )   # pyright: ignore[reportOperatorIssue]
    (
        create_process_folder
        >> generate_files_move_parameters
        >> move_to_processing
        >> generate_pdf_forms_l
        >> move_forms
        >> execute_forms_parser
        >> create_output_table_name
        >> create_output_table
        >> create_process_job_params
        >> execute_doc_processors
        >> import_docs_to_data_store
    )
