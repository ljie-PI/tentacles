#!/bin/bash

java -Dvertx.logger-delegate-factory-class-name=io.vertx.core.logging.SLF4JLogDelegateFactory \
    -jar ../build/libs/tentacles-0.1.0-with-dependencies.jar \
    ../build/resources/main/master_config.properties
