package com.lab_440.tentacles.master.datastore;

import com.lab_440.tentacles.common.Configuration;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

public class ListDatastore implements IDatastore {

    private List<String> itemList;
    private int size;

    public ListDatastore(Configuration conf) {
        itemList = new ArrayList<>();
    }

    @Override
    public boolean store(JsonObject item) {
        itemList.add(item.encode());
        size++;
        return true;
    }

    public int getSize() {
        return size;
    }

    public String get(int i) {
        return itemList.get(i);
    }

    public void clear() {
        size = 0;
        itemList.clear();
    }
}
