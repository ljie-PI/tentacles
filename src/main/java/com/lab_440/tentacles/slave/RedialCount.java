package com.lab_440.tentacles.slave;

public class RedialCount {

    private static long count;

    public static void inc() {
        count++;
    }

    public static long get() {
        return count;
    }
}
