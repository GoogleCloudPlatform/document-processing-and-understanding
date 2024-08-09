#!/usr/bin/env bash

# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -o errexit
set -o nounset

ERR_VARIABLE_NOT_DEFINED=2
ERR_MISSING_DEPENDENCY=3

CYAN='\033[0;36m'
BCYAN='\033[1;36m'
NC='\033[0m' # No Color
DIVIDER=$(printf %"$(tput cols)"s | tr " " "*")
DIVIDER+="\n"

# DECLARE VARIABLES
mapfile -t roles_array < project_apis.txt
mapfile -t roles_array < project_roles.txt


# DISPLAY HELPERS

section_open() {
    section_description=$1
    printf "$DIVIDER"
    printf "${CYAN}$section_description${NC} \n"
    printf "$DIVIDER"
}

section_close() {
    printf "$DIVIDER"
    printf "${CYAN}$section_description ${BCYAN}- done${NC}\n"
    printf "\n\n"
}

check_exec_dependency() {
  EXECUTABLE_NAME="${1}"

  if ! command -v "${EXECUTABLE_NAME}" >/dev/null 2>&1; then
    echo "[ERROR]: ${EXECUTABLE_NAME} command is not available, but it's needed. Make it available in PATH and try again. Terminating..."
    exit ${ERR_MISSING_DEPENDENCY}
  fi

  unset EXECUTABLE_NAME
}

check_exec_version() {
  EXECUTABLE_NAME="${1}"

  if ! "${EXECUTABLE_NAME}" --version 2>&1; then
    echo "[ERROR]: ${EXECUTABLE_NAME} command is not available, but it's needed. Make it available in PATH and try again. Terminating..."
    exit ${ERR_MISSING_DEPENDENCY}
  fi

  unset EXECUTABLE_NAME
}

check_environment_variable() {
  _VARIABLE_NAME=$1
  _ERROR_MESSAGE=$2
  _VARIABLE_VALUE="${!_VARIABLE_NAME:-}"
  if [ -z "${_VARIABLE_VALUE}" ]; then
    echo "[ERROR]: ${_VARIABLE_NAME} environment variable that points to ${_ERROR_MESSAGE} is not defined. Terminating..."
    exit ${ERR_VARIABLE_NOT_DEFINED}
  fi
  unset _VARIABLE_NAME
  unset _ERROR_MESSAGE
  unset _VARIABLE_VALUE
}

# shell script function to check if api is enabled
check_api_enabled(){
    local __api_endpoint=$1
    COUNTER=0
    MAX_TRIES=100
    while ! gcloud services list --project=$PROJECT_ID | grep -i $__api_endpoint && [ $COUNTER -lt $MAX_TRIES ]
    do
        sleep 6
        printf "."
        COUNTER=$((COUNTER + 1))
    done
    if [ $COUNTER -eq $MAX_TRIES ]; then
        echo "${__api_endpoint} api is not enabled, installation can not continue!"
        exit 1
    else
        echo "${__api_endpoint} api is enabled"
    fi
    unset __api_endpoint
}

# shell script function to check is policy rule is fullfilled set it if not set
check_and_set_policy_rule(){
  local _policy_name=$1 _rule_pattern=$2 _rule_set_pattern=$3 _project_id=$4
  echo "policy: ${_policy_name}"
  ## TODO: this only checks if a policy is set at the project, and ignores inherited policies. Use policy analyzer instead to check for effective policy enforcement https://cloud.google.com/policy-intelligence/docs/analyze-organization-policies#analyze_assets
  if ! gcloud org-policies describe $_policy_name --project="${PROJECT_ID}" | grep -i "${_rule_pattern}" ; then
    if ! set_policy_rule "${_policy_name}" "${_rule_set_pattern}" "${_project_id}" ; then
      echo "Org policy: '${_policy_name}' with rule: '${_rule_pattern}' cannot be set but is required, Contact your org-admin to set the policy before continue with deployment"
      exit 1
    fi
  fi
  unset _policy_name
  unset _rule_pattern
  unset _project_id
}

# shell script function to set policy rule
set_policy_rule(){
  local _policy_name=$1 _rule_pattern=$2 _project_id=$3
  local _policy_str="{
    \"name\": \"projects/${_project_id}/policies/${_policy_name}\",
    \"spec\": {
      \"rules\": [
        {
          ${_rule_pattern}
        }
      ]
    }
  }"
  gcloud org-policies set-policy <(echo $_policy_str)
  unset _policy_name
  unset _rule_pattern
  unset _project_id
  unset _policy_str
}

# shell script function to enable api
enable_api(){
    local __api_endpoint=$1
    gcloud services enable $__api_endpoint
    check_api_enabled $__api_endpoint
    unset __api_endpoint
}

# enable all apis in the array
enable_all_apis () {
    ## now loop through the above array
    for i in "${apis_array[@]}"
    do
      enable_api "$i"
    done
}

# shell script function to enable IAM roles
enable_role(){
    local __role=$1
    gcloud projects add-iam-policy-binding $PROJECT_ID --role=$1 --member=$__principal
    unset __role
}

# enable all roles in the roles array for service account used to deploy terraform resources
enable_deployer_roles () {
    local __principal=serviceAccount:$1
    ## now loop through the above array
    for i in "${roles_array[@]}"
    do
        enable_role "$i" "serviceAccount:$__principal"
    done
    unset __principal
}

# enable a specific set of roles for the default Compute SA implicitly used by Cloud Build.
# Behavior has changed since 2024 so that legacy Cloud Build SA no longer has permissions by default: https://cloud.google.com/build/docs/cloud-build-service-account-updates
enable_builder_roles () {
    local __PROJECTNUM=$(gcloud projects describe $PROJECT_ID --format="get(projectNumber)")
    local __principal=serviceAccount:"$__PROJECTNUM-compute@developer.gserviceaccount.com"
    ## necessary permissions for building AR
    for i in "roles/logging.logWriter" "roles/storage.objectUser" "roles/artifactregistry.createOnPushWriter"
    do
        enable_role "$i" "serviceAccount:$__principal"
    done
    unset __principal
    unset __PROJECTNUM
}
