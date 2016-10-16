package com.lab_440.tentacles.master.datastore;

import com.lab_440.tentacles.Configuration;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.io.*;

public class FileDatastore implements IDatastore {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private String filePath;
    private FileWriter fr;

    public FileDatastore(Configuration conf) {
        try {
            filePath = System.getProperty("user.dir")
                    + "/src/test/resources/filestore";
            fr = new FileWriter(filePath, true);
        } catch (IOException e) {
            logger.error("Could not open file for data storage!");
            System.exit(255);
        }
    }

    @Override
    public boolean store(JsonObject item) {
        try {
            fr.write(item.encode() + "\n");
            fr.flush();
        } catch (IOException e) {
            logger.error("Failed to store data to file storage");
            return false;
        }
        return true;
    }

}
