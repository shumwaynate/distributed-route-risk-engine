Set-Location (Join-Path $PSScriptRoot "..")

Write-Host ""
Write-Host "============================================================"
Write-Host "CUSTOM ROUTED ROUTE RISK API TEST"
Write-Host "============================================================"
Write-Host ""

# ============================================================
# CUSTOM ROUTE SETTINGS
# ============================================================
#
# Edit this section to test any two routable coordinates.
#
# Important:
# - OSRM works best with coordinates near public roads.
# - Random points in fields, mountains, lakes, or private roads may fail.
# - Coordinates should be in latitude/longitude order here.

$routeName = "Custom Route Risk Test"

$originLabel = "Custom Origin"
$originLatitude = 43.8231
$originLongitude = -111.7924

$destinationLabel = "Custom Destination"
$destinationLatitude = 43.4927
$destinationLongitude = -112.0408

$checkpointCount = 8

# Fallback condition used for checkpoints that do not match a road event.
# Options your scoring engine currently understands:
#   "normal"
#   "construction"
#   "wet"
#   "snowy"
#   "icy"
#   "closed"
$fallbackRoadCondition = "normal"

# Radius used to match supplied road events to generated route checkpoints.
$roadEventRadiusMiles = 1.0

# Set true to add nighttime scoring across the route.
$isNight = $false

# ============================================================
# OPTIONAL ROAD EVENTS
# ============================================================
#
# Add road events here to simulate construction, closures, icy roads, etc.
#
# Leave this empty if you want weather-only route risk:
#
#   $roadEvents = @()
#
# Event type examples:
#   "construction"
#   "work zone"
#   "road closure"
#   "icy"
#   "snowy"
#   "wet"

$roadEvents = @(
    @{
        event_id = "custom-demo-construction"
        event_type = "construction"
        description = "Example construction event near the route."
        latitude = 43.59723
        longitude = -111.965417
        source = "custom-script-road-event"
    }
)

# ============================================================
# ROUTED ROUTE RISK API TEST
# ============================================================

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

Write-Host "Submitting custom routed live-weather route-risk job..."
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
    Write-Host "ERROR: Failed to submit custom routed route-risk job."
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
Write-Host "CUSTOM ROUTED ROUTE RISK SUMMARY HIGHLIGHTS"
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

if ($summary.highest_risk_segment) {
    Write-Host ""
    Write-Host "Highest-risk segment:"
    Write-Host "Label: $($summary.highest_risk_segment.segment_label)"
    Write-Host "Latitude: $($summary.highest_risk_segment.latitude)"
    Write-Host "Longitude: $($summary.highest_risk_segment.longitude)"
    Write-Host "Weather source: $($summary.highest_risk_segment.weather.source)"
    Write-Host "Weather condition: $($summary.highest_risk_segment.weather.condition)"
    Write-Host "Temperature F: $($summary.highest_risk_segment.weather.temperature_f)"
    Write-Host "Wind MPH: $($summary.highest_risk_segment.weather.wind_mph)"
    Write-Host "Road condition: $($summary.highest_risk_segment.road_condition)"
    Write-Host "Road condition source: $($summary.highest_risk_segment.road_condition_source)"
    Write-Host "Score: $($summary.highest_risk_segment.risk_score)"
    Write-Host "Level: $($summary.highest_risk_segment.risk_level)"

    if ($summary.highest_risk_segment.matched_road_event) {
        Write-Host ""
        Write-Host "Matched road event:"
        Write-Host "ID: $($summary.highest_risk_segment.matched_road_event.event_id)"
        Write-Host "Type: $($summary.highest_risk_segment.matched_road_event.event_type)"
        Write-Host "Description: $($summary.highest_risk_segment.matched_road_event.description)"
        Write-Host "Distance miles: $($summary.highest_risk_segment.matched_road_event.distance_miles)"
    }

    if ($summary.highest_risk_segment.factors) {
        Write-Host ""
        Write-Host "Highest-risk factors:"
        foreach ($factor in $summary.highest_risk_segment.factors) {
            Write-Host "- $factor"
        }
    }
}

Write-Host ""
Write-Host "Summary:"
Write-Host $summary.summary

Write-Host ""
Write-Host "============================================================"
Write-Host "END CUSTOM ROUTED ROUTE RISK API TEST"
Write-Host "============================================================"
Write-Host ""