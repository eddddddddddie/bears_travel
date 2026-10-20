# Pipeline shortcuts. Run from bears_travel/. scrape and lines use the network (both cache their
# downloads), so they are separate opt-in steps and not part of `all`. Pass script flags with ARGS,
# e.g. make scrape ARGS=--refresh.
PYTHON ?= python
ARGS ?=

.PHONY: all scrape lines build elo model roads fixture draw assets test

all: build model roads fixture draw test

scrape:
	$(PYTHON) src/scrape_rlp.py $(ARGS)

lines:
	$(PYTHON) src/fetch_lines.py $(ARGS)

build:
	$(PYTHON) src/build_dataset.py

# optional: refit the Elo parameters and print values for config.py
elo:
	$(PYTHON) src/elo.py

model:
	$(PYTHON) src/travel_model.py

# Perth Bears two-game road trip plans (about 10 s)
roads:
	$(PYTHON) src/road_trips.py

# proposed Perth Bears 2027 fixture (about 15 s)
fixture:
	$(PYTHON) src/fixture_2027.py

# full 2027 competition draw around the Bears' fixture (about 70 s)
draw:
	$(PYTHON) src/draw_2027.py

# map outline and link-preview image for site/
assets:
	$(PYTHON) src/site_assets.py $(ARGS)

test:
	$(PYTHON) tests/test_pipeline.py
