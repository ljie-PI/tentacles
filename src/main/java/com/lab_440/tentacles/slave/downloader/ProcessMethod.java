package com.lab_440.tentacles.slave.downloader;

import java.lang.reflect.Method;

/**
 * Contains class method and priority
 */
public class ProcessMethod {

    private Method method;
    private int priority;

    public ProcessMethod(Method method, int priority) {
        this.method = method;
        this.priority = priority;
    }

    public Method getMethod() {
        return method;
    }

    public int getPriority() {
        return priority;
    }

}
