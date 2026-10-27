# R Plumber Animation Microservice (Phase 3).
# Receives an event payload, builds a bupaR eventlog, and returns a
# processanimateR htmlwidget animating the combined process: converted
# sessions (reached purchase) in orange, dropped sessions in grey.

library(bupaR)
library(processanimateR)
library(jsonlite)
library(htmlwidgets)

MAX_TOKENS <- 2000L  # display cap: uniform sample of the session cohort
                     # (tens of thousands of SVG tokens freeze the browser)
ANIM_DURATION <- 120L
HOP_FRACTION <- 0.04  # each edge hop takes ~4% of the animation duration

#* Health check
#* @get /health
#* @serializer unboxedJSON
function() {
  list(status = "ok")
}

#* Render combined process animation
#* @post /animate
#* Payload: { "dataset_version": str,
#*           "events": [{case_id, event_id, activity, timestamp, highlighted}] }
#* @serializer unboxedJSON
function(req, res) {
  # simplifyVector=TRUE converts the events array straight to a data.frame —
  # do.call(rbind, lapply(...)) on ~250k event objects hangs for minutes.
  body <- jsonlite::fromJSON(req$postBody)

  events <- as.data.frame(body$events, stringsAsFactors = FALSE)
  events$timestamp <- as.POSIXct(events$timestamp, tz = "UTC", format = "%Y-%m-%dT%H:%M:%OS")
  events$lifecycle <- "complete"
  events$resource <- "web_app"

  # Token cap: uniform downsampling to at most MAX_TOKENS animated tokens.
  # Downsample case_ids before building the eventlog (bupaR's filter_cases is
  # not exported in all versions).
  case_list <- unique(events$case_id)
  total_cases <- length(case_list)
  sampled <- total_cases > MAX_TOKENS
  if (sampled) {
    keep <- sample(case_list, MAX_TOKENS)
    events <- events[events$case_id %in% keep, ]
  }

  # Keep each case's real start time so tokens enter staggered across the
  # timeline (absolute mode), but re-space events within a case at a fixed
  # step so every token crosses each edge at a uniform speed regardless of
  # real inter-event gaps (seconds between clicks vs days between sessions).
  events <- events[order(events$case_id, events$timestamp, events$event_id), ]
  real_span <- as.numeric(difftime(max(events$timestamp), min(events$timestamp), units = "secs"))
  step_secs <- max(real_span * HOP_FRACTION, 1)
  idx <- ave(seq_len(nrow(events)), events$case_id, FUN = seq_along) - 1
  t0 <- ave(as.numeric(events$timestamp), events$case_id, FUN = min)
  events$timestamp <- as.POSIXct(t0 + idx * step_secs, origin = "1970-01-01", tz = "UTC")

  el <- bupaR::eventlog(
    events,
    case_id = "case_id",
    activity_id = "activity",
    activity_instance_id = "event_id",
    lifecycle_id = "lifecycle",
    resource_id = "resource",
    timestamp = "timestamp",
    order = "auto"
  )

  widget <- processanimateR::animate_process(
    el,
    mode = "absolute",
    duration = ANIM_DURATION,
    timeline = TRUE,
    legend = "color",
    mapping = processanimateR::token_aes(
      color = processanimateR::token_scale(
        "highlighted",
        scale = "ordinal",
        domain = c("converted", "dropped"),
        range = c("#ff7f0e", "#888888")
      )
    )
  )

  tmp <- tempfile(fileext = ".html")
  htmlwidgets::saveWidget(widget, tmp, selfcontained = TRUE)
  html <- paste(readLines(tmp, warn = FALSE), collapse = "\n")

  list(
    html = html,
    token_count = bupaR::n_cases(el),
    total_cases = total_cases,
    sampled = sampled
  )
}
