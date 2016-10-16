package com.lab_440.tentacles.slave.parser;

import java.lang.reflect.Method;
import java.util.regex.Pattern;

/**
 * Contains class uri prefix, method and priority
 */
public class ParseMethod {

    private Method method;
    private int priority;
    private Pattern uriPattern;

    public ParseMethod(Method method, int priority, Pattern uriPattern) {
        this.method = method;
        this.priority = priority;
        this.uriPattern = uriPattern;
    }

    public Method getMethod() {
        return method;
    }

    public int getPriority() {
        return priority;
    }

    public Pattern getUriPattern() {
        return uriPattern;
    }

}
