package com.lab_440.tentacles.slave.registration;

import io.vertx.core.Vertx;

public interface IRegistration {

    default public void registAll(Vertx vertx) {
        registDefault(vertx);
        regist(vertx);
    }

    public void registDefault(Vertx vertx);

    public void regist(Vertx vertx);

}
