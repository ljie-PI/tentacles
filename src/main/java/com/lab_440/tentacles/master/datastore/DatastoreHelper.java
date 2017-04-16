package com.lab_440.tentacles.master.datastore;

import com.lab_440.tentacles.common.Configuration;

import java.lang.reflect.Constructor;

public class DatastoreHelper {

    private volatile static IDatastore instance;

    public static void createInstance(Configuration conf) throws Exception {
        if (instance == null) {
            synchronized (DatastoreHelper.class) {
                if (instance == null) {
                    Class<?> clazz = Class.forName(conf.getDataStoreClass());
                    Constructor datastoreConstructor = clazz.getConstructor(Configuration.class);
                    instance = (IDatastore) datastoreConstructor.newInstance(conf);
                }
            }
        }
    }

    public static IDatastore getInstance() {
        return instance;
    }

}
