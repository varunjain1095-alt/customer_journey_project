FROM rocker/r-ver:4.4.1

# pandoc is required by htmlwidgets::saveWidget(selfcontained = TRUE)
RUN apt-get update && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

RUN install2.r --error plumber bupaR processanimateR jsonlite htmlwidgets

WORKDIR /app
COPY animate.R /app/animate.R

EXPOSE 8000
CMD ["R", "-e", "plumber::plumb('animate.R')$run(host = '0.0.0.0', port = 8000)"]
