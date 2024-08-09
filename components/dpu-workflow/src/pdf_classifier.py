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
#

"""Module for classifying PDF document one at a time."""
from typing import Optional

from google.api_core.client_options import ClientOptions # type: ignore # pylint: disable = no-name-in-module # pylint: disable = import-error
from google.cloud import documentai  # type: ignore # pylint: disable = no-name-in-module # pylint: disable = import-error
from google.cloud import storage # type: ignore # pylint: disable = no-name-in-module # pylint: disable = import-error

def is_form(
    project_id: str,
    location: str,
    processor_id: str,
    file_storage_bucket: str,
    file_path: str,
    mime_type: str,
    field_mask: Optional[str] = "entities",
    processor_version_id: Optional[str] = None,
) -> bool:
    """Function to check if PDF file contains forms."""
    # You must set the `api_endpoint` if you use a location other than "us".
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")

    client = documentai.DocumentProcessorServiceClient(client_options=opts)

    if processor_version_id:
        # The full resource name of the processor version, e.g.:
        # `projects/{project_id}/locations/{location}/processors/{processor_id}/
        # processorVersions/{processor_version_id}`
        name = client.processor_version_path(
            project_id, location, processor_id, processor_version_id
        )
    else:
        # The full resource name of the processor, e.g.:
        # `projects/{project_id}/locations/{location}/processors/{processor_id}`
        name = client.processor_path(project_id, location, processor_id)

    storage_client = storage.Client()
    bucket = storage_client.bucket(file_storage_bucket)
    blob = bucket.blob(file_path)

    # Download the file contents as bytes
    file_contents = blob.download_as_bytes()

    # Load binary data
    raw_document = documentai.RawDocument(content=file_contents, mime_type=mime_type)

    # For more information: https://cloud.google.com/document-ai/
    # docs/reference/rest/v1/ProcessOptions
    # Optional: Additional configurations for processing.
    process_options = documentai.ProcessOptions(
        # limit the number of pages processed to detect a form
        individual_page_selector=documentai.ProcessOptions.IndividualPageSelector(
            pages=[1, 2, 3, 4, 5]
        )
    )

    # Configure the process request
    request = documentai.ProcessRequest(
        name=name,
        raw_document=raw_document,
        field_mask=field_mask,
        process_options=process_options,
    )

    result = client.process_document(request=request)

    # For a full list of `Document` object attributes, reference this page:
    # https://cloud.google.com/document-ai/docs/reference/rest/v1/Document
    document = result.document

    for entity in document.entities:
        if entity.type.lower() == 'form' and float(entity.confidence) > 0.7:
            return True

    return False
