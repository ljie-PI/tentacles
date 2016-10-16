package com.lab_440.tentacles.master.datastore;

import io.vertx.core.json.JsonObject;

public interface IDatastore {

    public boolean store(JsonObject item);

}
