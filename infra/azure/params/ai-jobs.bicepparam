// AI jobs plane (Option A, owner decision 2026-10-01). No secrets: jobs read theirs from
// the Central US AI Key Vault at run time through their managed identities.
using '../main-ai-jobs.bicep'

// jobs_region. Read-only preflight 2026-10-01: PostgreSQL 18, zone-redundant HA, Front
// Door Private Link, availability zones, D4ds_v5 and ML quota all pass; Container Apps
// capacity can only be proven by creating the environment (step 1).
param jobsLocation = 'westus3'
param uniqueSuffix = readEnvironmentVariable('GBA_UNIQUE_SUFFIX')
param addressPrefix = '10.31.0.0/16'
param acaSubnetPrefix = '10.31.0.0/23'
param acrLoginServer = readEnvironmentVariable('GBA_ACR_LOGIN_SERVER')
param logAnalyticsWorkspaceId = readEnvironmentVariable('GBA_LOG_WORKSPACE_ID')
// Owner-approved steps, in order (see main-ai-jobs.bicep). Each is a separate approval.
param deployConnectivity = bool(readEnvironmentVariable('GBA_AI_JOBS_CONNECTIVITY', 'false'))
param netcheckImage = readEnvironmentVariable('GBA_AI_NETCHECK_IMAGE', '')
param deployJobs = bool(readEnvironmentVariable('GBA_AI_JOBS_DEPLOY', 'false'))
param aiWorkerImage = readEnvironmentVariable('GBA_AI_IMAGE', '')
