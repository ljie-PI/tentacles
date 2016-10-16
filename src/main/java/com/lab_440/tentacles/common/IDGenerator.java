package com.lab_440.tentacles.common;

import java.math.BigInteger;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Random;

public class IDGenerator {

    public static Random random = new Random(System.currentTimeMillis());

    public static String generateID(String str) {
        return strMD5(str);
    }

    public static String generateRandom() {
        long rl = random.nextLong();
        return strMD5(String.valueOf(rl));
    }

    public static void setSeed(long seed) {
        random.setSeed(seed);
    }

    private static String strMD5(String str) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            md.update(str.getBytes());
            return new BigInteger(1, md.digest()).toString(16);
        } catch (NoSuchAlgorithmException nae) {
            return str;
        }
    }
}
