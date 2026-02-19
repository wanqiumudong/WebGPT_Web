package com.fabgpt.usermanagement.repository;

import com.fabgpt.usermanagement.entity.User;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;
import java.util.Optional;

@Repository
public interface UserRepository extends JpaRepository<User, Long> {
    Optional<User> findByUsername(String username);
    
    @Query("SELECT u FROM User u WHERE u.username = ?1 AND u.password = ?2 AND u.status = 1")
    Optional<User> findByUsernameAndPasswordAndStatusActive(String username, String password);
    
    boolean existsByUsername(String username);
    boolean existsByEmail(String email);
}
