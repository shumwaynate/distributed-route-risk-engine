Set-Location (Join-Path $PSScriptRoot "..")

Write-Host ""
Write-Host "============================================================"
Write-Host "ROUTED ROUTE RISK API TEST WITH ROAD EVENTS"
Write-Host "============================================================"
Write-Host ""

# ============================================================
# EASY TEST SETTINGS
# ============================================================
#
# Change these values to test different routed route-risk scenarios.

$routeName = "Generated Rexburg to Idaho Falls Route Risk Test With Road Events"

$originLabel = "Rexburg, ID"
$originLatitude = 43.8231
$originLongitude = -111.7924

$destinationLabel = "Idaho Falls, ID"
$destinationLatitude = 43.4927
$destinationLongitude = -112.0408

$checkpointCount = 8

# Fallback road condition used when no road event matches a checkpoint.
# Try:
#   "normal"
#   "construction"
#   "wet"
#   "snowy"
#   "icy"
#   "closed"
$fallbackRoadCondition = "normal"

# Radius used to match supplied road events to generated route checkpoints.
$roadEventRadiusMiles = 1.0

# Try:
#   $false for daytime
#   $true for nighttime
$isNight = $false

# Demo road events near the generated Rexburg-to-Idaho-Falls route.
# Later, these can be replaced by events from WZDx / 511 / DOT feeds.
$roadEvents = @(
    @{
        event_id = "demo-work-zone-rigby"
        event_type = "construction"
        description = "Demo work zone near the Rigby area."
        latitude = 43.59723
        longitude = -111.965417
        source = "manual-demo-road-event"
    },
    @{
        event_id = "demo-road-closure-idaho-falls-north"
        event_type = "road closure"
        description = "Demo closure near north Idaho Falls."
        latitude = 43.540506
        longitude = -112.007668
        source = "manual-demo-road-event"
    }
)

# ============================================================
# ROUTED ROUTE RISK API TEST
# ============================================================
#
# Purpose:
# - Submit origin and destination coordinates to FastAPI.
# - Submit optional road events to FastAPI.
# - Let FastAPI call OSRM to generate a real route.
# - Let FastAPI sample checkpoints along that route.
# - Let FastAPI match road events to checkpoints.
# - Submit one live-weather Celery task per checkpoint.
# - Poll job status until the job finishes.
# - Fetch raw results.
# - Fetch the clean route-risk summary.
#
# Requirements:
# - Docker Desktop must be running.
# - FastAPI must be running.
# - Redis must be running.
# - Celery worker must be running.
# - Internet access must be available.
# - OSRM public API must be reachable.
# - Open-Meteo API must be reachable.
#
# Start the app first with:
#
#     .\scripts\start_dev.ps1
#
# Then run this script from the project root:
#
#     .\scripts\test_routed_route_risk_api.ps1

$body = @{
    route_name = $routeName

    origin_label = $originLabel
    origin_latitude = $originLatitude
    origin_longitude = $originLongitude

    destination_label = $destinationLabel
    destination_latitude = $destinationLatitude
    destination_longitude = $destinationLongitude

    checkpoint_count = $checkpointCount

    road_condition = $fallbackRoadCondition
    road_event_radius_miles = $roadEventRadiusMiles
    road_events = $roadEvents

    is_night = $isNight
} | ConvertTo-Json -Depth 20

Write-Host "Submitting routed live-weather route-risk job with road events..."
Write-Host ""

Write-Host "Test settings:"
Write-Host "Route name: $routeName"
Write-Host "Origin: $originLabel ($originLatitude, $originLongitude)"
Write-Host "Destination: $destinationLabel ($destinationLatitude, $destinationLongitude)"
Write-Host "Checkpoint count: $checkpointCount"
Write-Host "Fallback road condition: $fallbackRoadCondition"
Write-Host "Road event radius miles: $roadEventRadiusMiles"
Write-Host "Road event count: $($roadEvents.Count)"
Write-Host "Is night: $isNight"
Write-Host ""

try {
    $response = Invoke-RestMethod `
        -Uri "http://localhost:8000/submit_routed_route_risk_job" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body
} catch {
    Write-Host "ERROR: Failed to submit routed route-risk job."
    Write-Host "Make sure FastAPI is running at http://localhost:8000"
    Write-Host "Also make sure OSRM and Open-Meteo are reachable from your internet connection."
    Write-Host ""
    Write-Host $_
    exit 1
}

Write-Host "Submit response:"
$response | ConvertTo-Json -Depth 20

$jobId = $response.job_id

Write-Host ""
Write-Host "Saved job ID automatically:"
Write-Host $jobId

# ============================================================
# POLL JOB STATUS UNTIL COMPLETE
# ============================================================

Write-Host ""
Write-Host "Polling job status until complete..."
Write-Host ""

$maxAttempts = 60
$attempt = 0
$status = $null

while ($attempt -lt $maxAttempts) {
    $attempt++

    try {
        $status = Invoke-RestMethod `
            -Uri "http://localhost:8000/job_status/$jobId" `
            -Method Get
    } catch {
        Write-Host "ERROR: Failed to retrieve job status."
        Write-Host $_
        exit 1
    }

    Write-Host "Attempt $attempt/$maxAttempts - Status: $($status.status), Progress: $($status.progress_percent)%"

    if (
        $status.status -eq "SUCCESS" -or
        $status.status -eq "PARTIAL_FAILURE"
    ) {
        break
    }

    Start-Sleep -Seconds 2
}

if ($null -eq $status) {
    Write-Host "ERROR: No job status was returned."
    exit 1
}

if ($status.status -ne "SUCCESS" -and $status.status -ne "PARTIAL_FAILURE") {
    Write-Host ""
    Write-Host "ERROR: Job did not finish before timeout."
    Write-Host "Final observed status:"
    $status | ConvertTo-Json -Depth 20
    exit 1
}

Write-Host ""
Write-Host "Final job status:"
$status | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "Fetching raw job results..."
Write-Host ""

try {
    $results = Invoke-RestMethod `
        -Uri "http://localhost:8000/results/$jobId" `
        -Method Get
} catch {
    Write-Host "ERROR: Failed to retrieve raw job results."
    Write-Host $_
    exit 1
}

Write-Host "Raw job results:"
$results | ConvertTo-Json -Depth 50

Write-Host ""
Write-Host "Fetching clean route-risk summary..."
Write-Host ""

try {
    $summary = Invoke-RestMethod `
        -Uri "http://localhost:8000/route_risk_summary/$jobId" `
        -Method Get
} catch {
    Write-Host "ERROR: Failed to retrieve clean route-risk summary."
    Write-Host $_
    exit 1
}

Write-Host "Clean route-risk summary:"
$summary | ConvertTo-Json -Depth 50

Write-Host ""
Write-Host "============================================================"
Write-Host "ROUTED ROUTE RISK SUMMARY HIGHLIGHTS"
Write-Host "============================================================"
Write-Host ""

Write-Host "Route status: $($summary.route_status)"
Write-Host "Route name: $($summary.route_name)"
Write-Host "Origin: $($summary.origin)"
Write-Host "Destination: $($summary.destination)"
Write-Host "Route source: $($summary.route_source)"
Write-Host "Distance meters: $($summary.distance_meters)"
Write-Host "Duration seconds: $($summary.duration_seconds)"
Write-Host "Geometry point count: $($summary.geometry_point_count)"
Write-Host "Checkpoint count: $($summary.checkpoint_count)"
Write-Host "Segment count: $($summary.segment_count)"
Write-Host "Coordinate-enabled segment count: $($summary.coordinate_segment_count)"
Write-Host "Weather mode: $($summary.weather_mode)"
Write-Host "Road event count: $($summary.road_event_count)"
Write-Host "Matched road-event checkpoint count: $($summary.matched_road_event_checkpoint_count)"
Write-Host "Fallback road condition: $fallbackRoadCondition"
Write-Host "Road event radius miles: $roadEventRadiusMiles"
Write-Host "Test is night: $isNight"
Write-Host "Route risk score: $($summary.route_risk_score)"
Write-Host "Route risk level: $($summary.route_risk_level)"
Write-Host "Route blocked: $($summary.route_blocked)"
Write-Host "Average segment score: $($summary.average_segment_score)"
Write-Host "Blocking segment count: $($summary.blocking_segment_count)"

if ($summary.route_warning) {
    Write-Host "Route warning: $($summary.route_warning)"
}

if ($summary.highest_risk_segment) {
    Write-Host "Highest-risk segment: $($summary.highest_risk_segment.segment_label)"
    Write-Host "Highest-risk latitude: $($summary.highest_risk_segment.latitude)"
    Write-Host "Highest-risk longitude: $($summary.highest_risk_segment.longitude)"

    if ($summary.highest_risk_segment.weather) {
        Write-Host "Highest-risk weather source: $($summary.highest_risk_segment.weather.source)"
        Write-Host "Highest-risk weather condition: $($summary.highest_risk_segment.weather.condition)"
        Write-Host "Highest-risk temperature F: $($summary.highest_risk_segment.weather.temperature_f)"
        Write-Host "Highest-risk wind MPH: $($summary.highest_risk_segment.weather.wind_mph)"
    }

    Write-Host "Highest-risk road condition: $($summary.highest_risk_segment.road_condition)"
    Write-Host "Highest-risk road condition source: $($summary.highest_risk_segment.road_condition_source)"

    if ($summary.highest_risk_segment.matched_road_event) {
        Write-Host "Matched road event ID: $($summary.highest_risk_segment.matched_road_event.event_id)"
        Write-Host "Matched road event type: $($summary.highest_risk_segment.matched_road_event.event_type)"
        Write-Host "Matched road event description: $($summary.highest_risk_segment.matched_road_event.description)"
        Write-Host "Matched road event distance miles: $($summary.highest_risk_segment.matched_road_event.distance_miles)"
    }

    Write-Host "Highest-risk segment score: $($summary.highest_risk_segment.risk_score)"
    Write-Host "Highest-risk segment level: $($summary.highest_risk_segment.risk_level)"

    if ($summary.highest_risk_segment.factors) {
        Write-Host "Highest-risk factors:"
        foreach ($factor in $summary.highest_risk_segment.factors) {
            Write-Host "- $factor"
        }
    }
}

if ($summary.blocking_segments -and $summary.blocking_segments.Count -gt 0) {
    Write-Host ""
    Write-Host "Blocking segments:"

    foreach ($blockingSegment in $summary.blocking_segments) {
        Write-Host "- $($blockingSegment.segment_label)"
        Write-Host "  Road condition: $($blockingSegment.road_condition)"
        Write-Host "  Risk score: $($blockingSegment.risk_score)"
        Write-Host "  Risk level: $($blockingSegment.risk_level)"

        if ($blockingSegment.matched_road_event) {
            Write-Host "  Matched event: $($blockingSegment.matched_road_event.description)"
        }
    }
}

Write-Host ""
Write-Host "Summary:"
Write-Host $summary.summary

Write-Host ""
Write-Host "============================================================"
Write-Host "END ROUTED ROUTE RISK API TEST WITH ROAD EVENTS"
Write-Host "============================================================"
Write-Host ""