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

#!/bin/bash
# Bash script

##TODO: has an underlying dependency on legacy Cloud Build SA, so most new environments will fall back to Compute default SA. I added roles to Compute Sdefault SA to avoid permissions errors, but it would be better to explicitly specify a builder SA and not rely on default Compute SA

if ! gcloud artifacts repositories describe dpu-form-parser-repo --location=$REGION; then
    echo "repo not found"
    gcloud artifacts repositories create dpu-form-parser-repo --repository-format=docker --location=$REGION --description="repo build with cmd" --async
else
    echo "repo found"
fi

gcloud auth configure-docker us-central1-docker.pkg.dev
gcloud builds submit ../../components/processing/form_parser/src --pack image=$REGION-docker.pkg.dev/$PROJECT_ID/dpu-form-parser-repo/dpu-form-processor:latest --project $PROJECT_ID